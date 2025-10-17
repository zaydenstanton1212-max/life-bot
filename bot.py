import os
import discord
from discord.ext import commands, tasks
from discord.utils import get
import asyncio
import json
from datetime import datetime, timedelta
import pytz # For more robust timezone handling if needed, though not strictly required for timedelta checks

# --- Configuration ---
# You need to create a .env file in the same directory as this script
# and add your bot token like this:
# DISCORD_TOKEN=YOUR_BOT_TOKEN_HERE

# Load environment variables
from dotenv import load_dotenv
load_dotenv()
TOKEN = os.getenv('DISCORD_TOKEN')

# --- Your Discord User ID (Bot Owner) ---
# This ID will be used to grant you special permissions (e.g., !reloadconfig)
YOUR_DISCORD_USER_ID = 1428610161584508929 # Replace with your actual Discord User ID

CONFIG_FILE = 'config.json'
DEFAULT_CONFIG = {
    "prefix": "!",
    "owner_id": YOUR_DISCORD_USER_ID, # Automatically set your ID here
    "log_channel_id": None, # IMPORTANT: Replace with your desired Mod Log Channel ID (e.g., 123456789012345678)
    "mute_role_id": None, # Will be created if null
    "anti_raid": {
        "enabled": True,
        "join_threshold": 5,          # Number of joins
        "join_time_window_seconds": 10, # Within this many seconds
        "mass_mention_threshold": 5,  # Number of mentions in a single message
        "min_account_age_hours": 24,  # Accounts younger than this will be flagged/kicked
        "kick_on_spam_join": True,    # Kick users if join threshold is met (applies to young accounts too)
        "kick_on_mass_mention": True  # Kick users if mass mention threshold is met
    },
    "anti_nuke": {
        "enabled": True,
        "channel_delete_threshold": 3,    # Number of channel deletes
        "channel_delete_time_window_seconds": 10, # Within this many seconds
        "role_delete_threshold": 3,       # Number of role deletes
        "role_delete_time_window_seconds": 10,    # Within this many seconds
        "mass_ban_kick_threshold": 5,     # Number of bans/kicks
        "mass_ban_kick_time_window_seconds": 15   # Within this many seconds
    }
}

current_config = {} # Global variable to hold current configuration

def load_config():
    global current_config
    if not os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, 'w') as f:
            json.dump(DEFAULT_CONFIG, f, indent=4)
        print(f"Created default {CONFIG_FILE}. Please edit it with your bot's settings.")
        # Exit or pause to allow user to configure
        current_config = DEFAULT_CONFIG.copy() # Load defaults for immediate start, but warn
        return current_config
    with open(CONFIG_FILE, 'r') as f:
        try:
            loaded_config = json.load(f)
            # Merge loaded config with default to ensure all keys are present
            # This handles cases where new config options are added
            for key, value in DEFAULT_CONFIG.items():
                if key not in loaded_config:
                    loaded_config[key] = value
                elif isinstance(value, dict) and isinstance(loaded_config[key], dict):
                    for sub_key, sub_value in value.items():
                        if sub_key not in loaded_config[key]:
                            loaded_config[key][sub_key] = sub_value
            current_config = loaded_config
            return current_config
        except json.JSONDecodeError:
            print(f"Error: {CONFIG_FILE} is malformed. Using default configuration.")
            current_config = DEFAULT_CONFIG.copy()
            return current_config

# Load config initially
config = load_config()

# --- Bot Setup ---
intents = discord.Intents.default()
intents.message_content = True # Needed for reading message content (commands, mass mentions)
intents.members = True         # Needed for moderation, anti-raid (member join/leave, account age)
intents.presences = True       # Useful for future features, though not strictly required for current ones
intents.bans = True            # Needed for on_member_remove audit log checks

bot = commands.Bot(command_prefix=config['prefix'], intents=intents, owner_id=config['owner_id'])
bot.start_time = datetime.utcnow() # Store bot's start time for uptime command

# --- Global Data for Anti-Raid/Nuke ---
member_joins = {} # Stores (timestamp, member_id) per guild: {'guild_id': [(timestamp, member_id), ...]}
guild_actions = {} # Stores actions per guild for anti-nuke: {'guild_id': {'channel_deletes': [], 'role_deletes': [], 'member_bans_kicks': []}}

# --- Persistent Data (Warnings) ---
WARNINGS_FILE = 'warnings.json'
warnings_data = {}

def load_warnings():
    global warnings_data
    if os.path.exists(WARNINGS_FILE):
        with open(WARNINGS_FILE, 'r') as f:
            try:
                warnings_data = json.load(f)
            except json.JSONDecodeError:
                print(f"Error: {WARNINGS_FILE} is malformed. Initializing with empty warnings.")
                warnings_data = {} # Return empty if file is corrupt
    else:
        warnings_data = {}
    return warnings_data

def save_warnings():
    with open(WARNINGS_FILE, 'w') as f:
        json.dump(warnings_data, f, indent=4)

load_warnings() # Load warnings at bot start

# --- Helper Functions ---
async def get_log_channel(guild):
    log_channel_id = config.get('log_channel_id')
    if log_channel_id:
        channel = guild.get_channel(log_channel_id)
        if not channel:
            print(f"Warning: Log channel with ID {log_channel_id} not found in guild {guild.name}.")
        return channel
    return None

async def log_action(guild, embed):
    log_channel = await get_log_channel(guild)
    if log_channel:
        try:
            await log_channel.send(embed=embed)
        except discord.Forbidden:
            print(f"Warning: Bot does not have permissions to send messages in log channel {log_channel.name} in guild {guild.name}.")
    else:
        # Fallback to printing to console if no log channel is configured or found
        print(f"No log channel configured or found for guild {guild.name}. Log:\n{embed.description}")
        if embed.fields:
            for field in embed.fields:
                print(f"  {field.name}: {field.value}")

async def ensure_mute_role(guild):
    # Ensure current_config is used for checking mute_role_id
    mute_role_id = current_config.get('mute_role_id')
    if mute_role_id:
        role = guild.get_role(mute_role_id)
        if role:
            return role

    role = get(guild.roles, name="Muted")
    if not role:
        print(f"Mute role not found, creating one for {guild.name}...")
        try:
            # Create role with minimal permissions, then deny send_messages in all channels
            role = await guild.create_role(name="Muted", permissions=discord.Permissions(send_messages=False, read_messages=True))
            # Deny permissions to send messages in existing channels
            for channel in guild.channels:
                try:
                    await channel.set_permissions(role, send_messages=False, add_reactions=False, speak=False, connect=False)
                except discord.Forbidden:
                    print(f"Could not set permissions for channel {channel.name} for Muted role (missing permissions).")
            
            # Update config with new role ID and save it
            current_config['mute_role_id'] = role.id
            with open(CONFIG_FILE, 'w') as f:
                json.dump(current_config, f, indent=4)
            print(f"Mute role '{role.name}' created with ID {role.id} and saved to config.json")
        except discord.Forbidden:
            print(f"Bot does not have permissions to create roles in guild {guild.name}. (Manage Roles required)")
            return None
    return role

def is_owner(ctx):
    return ctx.author.id == bot.owner_id

# --- Events ---
@bot.event
async def on_ready():
    print(f'Logged in as {bot.user.name} ({bot.user.id})')
    print('------')
    await bot.change_presence(activity=discord.Activity(type=discord.ActivityType.listening, name=f"{config['prefix']}help"))

    # Ensure mute role exists for all guilds the bot is in
    for guild in bot.guilds:
        await ensure_mute_role(guild)

    # Initialize global data structures for existing guilds
    for guild in bot.guilds:
        if guild.id not in guild_actions:
            guild_actions[guild.id] = {
                'channel_deletes': [],
                'role_deletes': [],
                'member_bans_kicks': []
            }
        if guild.id not in member_joins:
            member_joins[guild.id] = []


@bot.event
async def on_guild_join(guild):
    print(f"Joined guild: {guild.name} ({guild.id})")
    await ensure_mute_role(guild)
    if guild.id not in guild_actions:
        guild_actions[guild.id] = {
            'channel_deletes': [],
            'role_deletes': [],
            'member_bans_kicks': []
        }
    if guild.id not in member_joins:
        member_joins[guild.id] = []

@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.MissingRequiredArgument):
        await ctx.send(f"Error: Missing argument(s). Usage: `{config['prefix']}help {ctx.command.name}`")
    elif isinstance(error, commands.BadArgument):
        await ctx.send(f"Error: Invalid argument(s). Usage: `{config['prefix']}help {ctx.command.name}`")
    elif isinstance(error, commands.MissingPermissions):
        await ctx.send("Error: You don't have the necessary permissions to use this command.")
    elif isinstance(error, commands.BotMissingPermissions):
        missing_perms_str = '`, `'.join([p.replace('_', ' ').title() for p in error.missing_perms])
        await ctx.send(f"Error: I don't have the necessary permissions to perform this action. I need: `{missing_perms_str}`")
    elif isinstance(error, commands.CommandNotFound):
        pass # Ignore if command doesn't exist
    elif isinstance(error, commands.MemberNotFound):
        await ctx.send(f"Error: Member not found. Please provide a valid member (mention, ID, or name#discriminator).")
    elif isinstance(error, commands.UserNotFound):
        await ctx.send(f"Error: User not found. Please provide a valid user ID or name#discriminator.")
    else:
        print(f"Unhandled command error in guild {ctx.guild.name} by {ctx.author.name}: {error}")
        await ctx.send(f"An unexpected error occurred while executing the command: `{error}`")

# --- Anti-Raid/Nuke Logic ---

@bot.event
async def on_member_join(member):
    guild_id = member.guild.id
    if not config['anti_raid']['enabled']:
        return

    now = datetime.utcnow()
    
    # Account Age Check
    min_account_age_hours = config['anti_raid']['min_account_age_hours']
    if min_account_age_hours > 0:
        account_age = now - member.created_at
        if account_age < timedelta(hours=min_account_age_hours):
            embed = discord.Embed(
                title="🚨 Account Age Warning 🚨",
                description=f"{member.mention} ({member.id}) joined but has a new account.",
                color=discord.Color.orange()
            )
            embed.add_field(name="Account Created", value=member.created_at.strftime("%Y-%m-%d %H:%M:%S UTC"), inline=True)
            embed.add_field(name="Age", value=f"{account_age.total_seconds() / 3600:.1f} hours", inline=True)
            embed.set_footer(text="Considered a potential raid account.")
            
            action_taken = False
            if config['anti_raid']['kick_on_spam_join']:
                 try:
                    await member.kick(reason=f"Account too young ({account_age.total_seconds() / 3600:.1f} hours). Anti-raid policy.")
                    # Removed system channel message as log_action covers it better
                    print(f"Kicked {member.display_name} for being a new account.")
                    embed.add_field(name="Action", value="Kicked (Too young)", inline=False)
                    action_taken = True
                 except discord.Forbidden:
                    print(f"Failed to kick {member.display_name}: Missing permissions.")
            await log_action(member.guild, embed)
            if action_taken: return # If kicked, no need for further join checks for this member

    # Join Spike Detection
    if guild_id not in member_joins:
        member_joins[guild_id] = []
    
    member_joins[guild_id].append((now, member.id))
    
    # Filter out old joins for this guild
    time_window = timedelta(seconds=config['anti_raid']['join_time_window_seconds'])
    member_joins[guild_id][:] = [(t, id) for t, id in member_joins[guild_id] if now - t < time_window]

    if len(member_joins[guild_id]) >= config['anti_raid']['join_threshold']:
        embed = discord.Embed(
            title="⚠️ Possible Raid Detected ⚠️",
            description=f"**{len(member_joins[guild_id])} members joined in the last {config['anti_raid']['join_time_window_seconds']} seconds!**",
            color=discord.Color.red()
        )
        # Show actual members who joined, only mention valid users
        member_list = []
        for _, m_id in member_joins[guild_id]:
            m = member.guild.get_member(m_id)
            if m: member_list.append(m.mention)
            else: member_list.append(f"<@{m_id}> (left)")
        
        embed.add_field(name="Details", value="\n".join(member_list), inline=False)
        embed.set_footer(text="Consider locking the server or enabling verification.")
        
        if config['anti_raid']['kick_on_spam_join']:
            kicked_members = []
            for _, member_id in list(member_joins[guild_id]): # Iterate over a copy
                member_to_kick = member.guild.get_member(member_id)
                if member_to_kick and not member_to_kick.bot:
                    try:
                        await member_to_kick.kick(reason="Detected as part of a join raid.")
                        kicked_members.append(f"{member_to_kick.mention}")
                        print(f"Kicked {member_to_kick.display_name} due to raid detection.")
                    except discord.Forbidden:
                        print(f"Failed to kick {member_to_kick.display_name}: Missing permissions.")
                # Always remove from list after attempted action to prevent double processing
                member_joins[guild_id][:] = [(t, id) for t, id in member_joins[guild_id] if id != member_id]

            if kicked_members:
                embed.add_field(name="Action", value=f"Attempted to kick detected raid members:\n{', '.join(kicked_members)}", inline=False)
            member_joins[guild_id].clear() # Clear the list after taking action for this guild

        await log_action(member.guild, embed)


@bot.event
async def on_message(message):
    # This needs to be the first line in on_message to ensure commands are processed
    await bot.process_commands(message)

    if message.author.bot or not config['anti_raid']['enabled']:
        return

    # Mass Mention Detection
    if config['anti_raid']['mass_mention_threshold'] > 0:
        mentions_count = len(message.mentions) + len(message.role_mentions)
        if mentions_count >= config['anti_raid']['mass_mention_threshold']:
            embed = discord.Embed(
                title="🚫 Mass Mention Detected 🚫",
                description=f"{message.author.mention} ({message.author.id}) sent a message with **{mentions_count} mentions**.",
                color=discord.Color.dark_red()
            )
            embed.add_field(name="Channel", value=message.channel.mention, inline=True)
            embed.add_field(name="Message Link", value=message.jump_url, inline=True)
            embed.set_footer(text="Potentially a raid or spam attempt.")
            await log_action(message.guild, embed)

            if config['anti_raid']['kick_on_mass_mention']:
                try:
                    await message.author.kick(reason="Mass mentioning detected. Anti-raid policy.")
                    await message.channel.send(f"Kicked {message.author.mention} for mass mentioning.")
                    print(f"Kicked {message.author.display_name} for mass mentioning.")
                    embed.add_field(name="Action", value="Kicked", inline=False)
                    await log_action(message.guild, embed)
                except discord.Forbidden:
                    print(f"Failed to kick {message.author.display_name}: Missing permissions.")
                try:
                    await message.delete() # Delete the offending message
                except discord.Forbidden:
                    print("Failed to delete mass mention message: Missing permissions (Manage Messages).")


@bot.event
async def on_guild_channel_delete(channel):
    if not config['anti_nuke']['enabled']:
        return

    guild_id = channel.guild.id
    if guild_id not in guild_actions:
        guild_actions[guild_id] = {'channel_deletes': [], 'role_deletes': [], 'member_bans_kicks': []}

    now = datetime.utcnow()
    guild_actions[guild_id]['channel_deletes'].append(now)
    guild_actions[guild_id]['channel_deletes'][:] = [
        t for t in guild_actions[guild_id]['channel_deletes']
        if now - t < timedelta(seconds=config['anti_nuke']['channel_delete_time_window_seconds'])
    ]

    embed = discord.Embed(
        title="💥 Possible Nuke Detected (Channel Deletions) 💥",
        description=f"A channel `{channel.name}` (ID: {channel.id}) was deleted.",
        color=discord.Color.dark_red()
    )
    embed.add_field(name="Current Count", value=f"{len(guild_actions[guild_id]['channel_deletes'])} channels deleted in "
                                                f"the last {config['anti_nuke']['channel_delete_time_window_seconds']}s", inline=False)
    embed.timestamp = now

    deleter = "Unknown"
    try:
        await asyncio.sleep(1) # Give audit log a moment to catch up
        async for entry in channel.guild.audit_logs(limit=1, action=discord.AuditLogAction.channel_delete):
            if entry.target.id == channel.id and (datetime.utcnow() - entry.created_at).total_seconds() < 5:
                deleter = f"{entry.user.mention} ({entry.user.id})"
                break
    except discord.Forbidden:
        print(f"Bot missing 'View Audit Log' permission for guild {channel.guild.name}.")
    
    embed.add_field(name="Deleted By", value=deleter, inline=False)

    if len(guild_actions[guild_id]['channel_deletes']) >= config['anti_nuke']['channel_delete_threshold']:
        embed.description += f"\n**Mass deletion threshold reached! ({len(guild_actions[guild_id]['channel_deletes'])}/{config['anti_nuke']['channel_delete_threshold']})**"
        embed.set_footer(text="Consider revoking permissions or backing up server.")
        guild_actions[guild_id]['channel_deletes'].clear() # Clear to prevent repeated alerts
    
    await log_action(channel.guild, embed)


@bot.event
async def on_guild_role_delete(role):
    if not config['anti_nuke']['enabled']:
        return

    guild_id = role.guild.id
    if guild_id not in guild_actions:
        guild_actions[guild_id] = {'channel_deletes': [], 'role_deletes': [], 'member_bans_kicks': []}

    now = datetime.utcnow()
    guild_actions[guild_id]['role_deletes'].append(now)
    guild_actions[guild_id]['role_deletes'][:] = [
        t for t in guild_actions[guild_id]['role_deletes']
        if now - t < timedelta(seconds=config['anti_nuke']['role_delete_time_window_seconds'])
    ]

    embed = discord.Embed(
        title="💥 Possible Nuke Detected (Role Deletions) 💥",
        description=f"A role `{role.name}` (ID: {role.id}) was deleted.",
        color=discord.Color.dark_red()
    )
    embed.add_field(name="Current Count", value=f"{len(guild_actions[guild_id]['role_deletes'])} roles deleted in "
                                                f"the last {config['anti_nuke']['role_delete_time_window_seconds']}s", inline=False)
    embed.timestamp = now

    deleter = "Unknown"
    try:
        await asyncio.sleep(1) # Give audit log a moment to catch up
        async for entry in role.guild.audit_logs(limit=1, action=discord.AuditLogAction.role_delete):
            if entry.target.id == role.id and (datetime.utcnow() - entry.created_at).total_seconds() < 5:
                deleter = f"{entry.user.mention} ({entry.user.id})"
                break
    except discord.Forbidden:
        print(f"Bot missing 'View Audit Log' permission for guild {role.guild.name}.")
    
    embed.add_field(name="Deleted By", value=deleter, inline=False)

    if len(guild_actions[guild_id]['role_deletes']) >= config['anti_nuke']['role_delete_threshold']:
        embed.description += f"\n**Mass deletion threshold reached! ({len(guild_actions[guild_id]['role_deletes'])}/{config['anti_nuke']['role_delete_threshold']})**"
        embed.set_footer(text="Consider revoking permissions or backing up server.")
        guild_actions[guild_id]['role_deletes'].clear() # Clear to prevent repeated alerts
    
    await log_action(role.guild, embed)


@bot.event
async def on_member_remove(member):
    # This event triggers for both kicks and bans. We'll use audit logs to differentiate.
    if not config['anti_nuke']['enabled']:
        return

    await asyncio.sleep(1) # Give audit log a moment to catch up

    guild_id = member.guild.id
    if guild_id not in guild_actions:
        guild_actions[guild_id] = {'channel_deletes': [], 'role_deletes': [], 'member_bans_kicks': []}

    action_type = None
    action_executor = "Unknown"

    try:
        # Check audit logs for recent kicks/bans involving this member
        async for entry in member.guild.audit_logs(limit=3, action=discord.AuditLogAction.kick):
            if entry.target == member and (datetime.utcnow() - entry.created_at).total_seconds() < 5:
                action_type = "kick"
                action_executor = f"{entry.user.mention} ({entry.user.id})"
                guild_actions[guild_id]['member_bans_kicks'].append(datetime.utcnow())
                break
        
        if not action_type: # If not kicked, check for ban
            async for entry in member.guild.audit_logs(limit=3, action=discord.AuditLogAction.ban):
                if entry.target == member and (datetime.utcnow() - entry.created_at).total_seconds() < 5:
                    action_type = "ban"
                    action_executor = f"{entry.user.mention} ({entry.user.id})"
                    guild_actions[guild_id]['member_bans_kicks'].append(datetime.utcnow())
                    break
    except discord.Forbidden:
        print(f"Bot missing 'View Audit Log' permission for guild {member.guild.name}. Anti-nuke mass ban/kick detection may be impaired.")
        return

    # If it's a kick or ban, log it and check for mass action
    if action_type:
        now = datetime.utcnow()
        guild_actions[guild_id]['member_bans_kicks'][:] = [
            t for t in guild_actions[guild_id]['member_bans_kicks']
            if now - t < timedelta(seconds=config['anti_nuke']['mass_ban_kick_time_window_seconds'])
        ]

        embed = discord.Embed(
            title=f"🚨 Member {action_type.capitalize()}ed 🚨",
            description=f"{member.mention} ({member.id}) was {action_type}ed.",
            color=discord.Color.red()
        )
        embed.add_field(name="Action By", value=action_executor, inline=False)
        embed.add_field(name="Current Count", value=f"{len(guild_actions[guild_id]['member_bans_kicks'])} members {action_type}ed in "
                                                    f"the last {config['anti_nuke']['mass_ban_kick_time_window_seconds']}s", inline=False)
        embed.timestamp = now

        if len(guild_actions[guild_id]['member_bans_kicks']) >= config['anti_nuke']['mass_ban_kick_threshold']:
            embed.description += f"\n**Mass {action_type} threshold reached! ({len(guild_actions[guild_id]['member_bans_kicks'])}/{config['anti_nuke']['mass_ban_kick_threshold']})**"
            embed.set_footer(text="Consider revoking permissions of recent moderators.")
            guild_actions[guild_id]['member_bans_kicks'].clear() # Clear to prevent repeated alerts
        
        await log_action(member.guild, embed)


# --- Moderation Commands ---

@bot.command(name='kick', help='Kicks a member from the server.')
@commands.has_permissions(kick_members=True)
@commands.bot_has_permissions(kick_members=True)
async def kick(ctx, member: discord.Member, *, reason="No reason provided."):
    if member == ctx.author:
        return await ctx.send("You cannot kick yourself!")
    if member.bot and not ctx.author.id == bot.owner_id:
        return await ctx.send("You cannot kick a bot unless you are the bot owner.")
    if member.top_role >= ctx.author.top_role and ctx.author.id != bot.owner_id:
        return await ctx.send("You cannot kick someone with an equal or higher role than yourself.")
    if member.top_role >= ctx.guild.me.top_role:
        return await ctx.send("I cannot kick someone with an equal or higher role than myself.")

    try:
        await member.kick(reason=reason)
        embed = discord.Embed(
            title="👢 Member Kicked",
            description=f"{member.mention} has been kicked by {ctx.author.mention}.",
            color=discord.Color.orange()
        )
        embed.add_field(name="Member", value=f"{member.name}#{member.discriminator} ({member.id})", inline=False)
        embed.add_field(name="Moderator", value=f"{ctx.author.name}#{ctx.author.discriminator} ({ctx.author.id})", inline=False)
        embed.add_field(name="Reason", value=reason, inline=False)
        embed.timestamp = datetime.utcnow()
        embed.set_footer(text=f"User ID: {member.id}")

        await ctx.send(f"✅ Kicked {member.mention}.")
        await log_action(ctx.guild, embed)
    except discord.Forbidden:
        await ctx.send("I don't have permission to kick that member.")
    except Exception as e:
        await ctx.send(f"An error occurred: {e}")

@bot.command(name='ban', help='Bans a member from the server.')
@commands.has_permissions(ban_members=True)
@commands.bot_has_permissions(ban_members=True)
async def ban(ctx, member: discord.Member, *, reason="No reason provided."):
    if member == ctx.author:
        return await ctx.send("You cannot ban yourself!")
    if member.bot and not ctx.author.id == bot.owner_id:
        return await ctx.send("You cannot ban a bot unless you are the bot owner.")
    if member.top_role >= ctx.author.top_role and ctx.author.id != bot.owner_id:
        return await ctx.send("You cannot ban someone with an equal or higher role than yourself.")
    if member.top_role >= ctx.guild.me.top_role:
        return await ctx.send("I cannot ban someone with an equal or higher role than myself.")

    try:
        await member.ban(reason=reason)
        embed = discord.Embed(
            title="🔨 Member Banned",
            description=f"{member.mention} has been banned by {ctx.author.mention}.",
            color=discord.Color.red()
        )
        embed.add_field(name="Member", value=f"{member.name}#{member.discriminator} ({member.id})", inline=False)
        embed.add_field(name="Moderator", value=f"{ctx.author.name}#{ctx.author.discriminator} ({ctx.author.id})", inline=False)
        embed.add_field(name="Reason", value=reason, inline=False)
        embed.timestamp = datetime.utcnow()
        embed.set_footer(text=f"User ID: {member.id}")

        await ctx.send(f"✅ Banned {member.mention}.")
        await log_action(ctx.guild, embed)
    except discord.Forbidden:
        await ctx.send("I don't have permission to ban that member.")
    except Exception as e:
        await ctx.send(f"An error occurred: {e}")

@bot.command(name='unban', help='Unbans a user by their ID or name#discriminator.')
@commands.has_permissions(ban_members=True)
@commands.bot_has_permissions(ban_members=True)
async def unban(ctx, user_id_or_name: str, *, reason="No reason provided."):
    try:
        # Try to fetch by ID first
        user = None
        try:
            user_id = int(user_id_or_name)
            user = await bot.fetch_user(user_id)
        except ValueError:
            pass # Not a pure ID, try by name#discriminator

        if not user:
            # Search through banned entries by name#discriminator
            banned_users = [entry.user async for entry in ctx.guild.bans()]
            for banned_user in banned_users:
                if str(banned_user).lower() == user_id_or_name.lower():
                    user = banned_user
                    break
        
        if not user:
            return await ctx.send(f"Could not find a banned user matching `{user_id_or_name}`.")

        await ctx.guild.unban(user, reason=reason)
        embed = discord.Embed(
            title="🔓 User Unbanned",
            description=f"{user.mention} has been unbanned by {ctx.author.mention}.",
            color=discord.Color.green()
        )
        embed.add_field(name="User", value=f"{user.name}#{user.discriminator} ({user.id})", inline=False)
        embed.add_field(name="Moderator", value=f"{ctx.author.name}#{ctx.author.discriminator} ({ctx.author.id})", inline=False)
        embed.add_field(name="Reason", value=reason, inline=False)
        embed.timestamp = datetime.utcnow()
        embed.set_footer(text=f"User ID: {user.id}")

        await ctx.send(f"✅ Unbanned {user.mention}.")
        await log_action(ctx.guild, embed)
    except discord.Forbidden:
        await ctx.send("I don't have permission to unban that user.")
    except discord.NotFound:
        await ctx.send("That user is not currently banned.")
    except Exception as e:
        await ctx.send(f"An error occurred: {e}")

@bot.command(name='mute', help='Mutes a member in the server. Usage: !mute <member> [duration_minutes] [reason]')
@commands.has_permissions(kick_members=True) # Mute typically requires kick_members or manage_roles
@commands.bot_has_permissions(manage_roles=True)
async def mute(ctx, member: discord.Member, duration_minutes: int = 0, *, reason="No reason provided."):
    if member == ctx.author:
        return await ctx.send("You cannot mute yourself!")
    if member.bot and not ctx.author.id == bot.owner_id:
        return await ctx.send("You cannot mute a bot unless you are the bot owner.")
    if member.top_role >= ctx.author.top_role and ctx.author.id != bot.owner_id:
        return await ctx.send("You cannot mute someone with an equal or higher role than yourself.")
    if member.top_role >= ctx.guild.me.top_role:
        return await ctx.send("I cannot mute someone with an equal or higher role than myself.")

    mute_role = await ensure_mute_role(ctx.guild)
    if not mute_role:
        return await ctx.send("Failed to create/find mute role. Please ensure I have 'Manage Roles' permission.")

    if mute_role in member.roles:
        return await ctx.send(f"{member.mention} is already muted.")

    try:
        await member.add_roles(mute_role, reason=reason)
        
        duration_message = ""
        if duration_minutes > 0:
            duration_message = f" for {duration_minutes} minutes"
            # Schedule unmute
            await ctx.send(f"Muted {member.mention}{duration_message}. Scheduling unmute.")
            await asyncio.sleep(duration_minutes * 60)
            
            # Check if still muted and role exists before attempting unmute
            if mute_role in member.roles and get(ctx.guild.roles, id=mute_role.id): 
                await member.remove_roles(mute_role, reason="Mute duration expired.")
                await ctx.send(f"✅ Unmuted {member.mention} (mute duration expired).")
                log_embed = discord.Embed(
                    title="🔈 Member Unmuted (Auto)",
                    description=f"{member.mention} has been automatically unmuted after {duration_minutes} minutes.",
                    color=discord.Color.blue()
                )
                log_embed.add_field(name="User", value=f"{member.name}#{member.discriminator} ({member.id})", inline=False)
                log_embed.add_field(name="Reason", value="Mute duration expired", inline=False)
                log_embed.timestamp = datetime.utcnow()
                await log_action(ctx.guild, log_embed)
        else:
            await ctx.send(f"✅ Muted {member.mention}.")

        embed = discord.Embed(
            title="🔇 Member Muted",
            description=f"{member.mention} has been muted by {ctx.author.mention}{duration_message}.",
            color=discord.Color.purple()
        )
        embed.add_field(name="Member", value=f"{member.name}#{member.discriminator} ({member.id})", inline=False)
        embed.add_field(name="Moderator", value=f"{ctx.author.name}#{ctx.author.discriminator} ({ctx.author.id})", inline=False)
        embed.add_field(name="Duration", value=f"{duration_minutes} minutes" if duration_minutes > 0 else "Permanent", inline=True)
        embed.add_field(name="Reason", value=reason, inline=False)
        embed.timestamp = datetime.utcnow()
        embed.set_footer(text=f"User ID: {member.id}")

        await log_action(ctx.guild, embed)

    except discord.Forbidden:
        await ctx.send("I don't have permission to manage roles (mute).")
    except Exception as e:
        await ctx.send(f"An error occurred: {e}")

@bot.command(name='unmute', help='Unmutes a member in the server.')
@commands.has_permissions(kick_members=True) # Unmute typically requires kick_members or manage_roles
@commands.bot_has_permissions(manage_roles=True)
async def unmute(ctx, member: discord.Member, *, reason="No reason provided."):
    mute_role = await ensure_mute_role(ctx.guild)
    if not mute_role:
        return await ctx.send("Mute role not found. Cannot unmute.")

    if mute_role not in member.roles:
        return await ctx.send(f"{member.mention} is not currently muted.")

    try:
        await member.remove_roles(mute_role, reason=reason)
        embed = discord.Embed(
            title="🔈 Member Unmuted",
            description=f"{member.mention} has been unmuted by {ctx.author.mention}.",
            color=discord.Color.blue()
        )
        embed.add_field(name="Member", value=f"{member.name}#{member.discriminator} ({member.id})", inline=False)
        embed.add_field(name="Moderator", value=f"{ctx.author.name}#{ctx.author.discriminator} ({ctx.author.id})", inline=False)
        embed.add_field(name="Reason", value=reason, inline=False)
        embed.timestamp = datetime.utcnow()
        embed.set_footer(text=f"User ID: {member.id}")

        await ctx.send(f"✅ Unmuted {member.mention}.")
        await log_action(ctx.guild, embed)
    except discord.Forbidden:
        await ctx.send("I don't have permission to manage roles (unmute).")
    except Exception as e:
        await ctx.send(f"An error occurred: {e}")

@bot.command(name='warn', help='Issues a warning to a member.')
@commands.has_permissions(kick_members=True) # Or a custom role/permission check
@commands.bot_has_permissions(send_messages=True)
async def warn(ctx, member: discord.Member, *, reason="No reason provided."):
    if member == ctx.author:
        return await ctx.send("You cannot warn yourself!")
    if member.bot and not ctx.author.id == bot.owner_id:
        return await ctx.send("You cannot warn a bot unless you are the bot owner.")
    if member.top_role >= ctx.author.top_role and ctx.author.id != bot.owner_id:
        return await ctx.send("You cannot warn someone with an equal or higher role than yourself.")

    guild_id_str = str(ctx.guild.id)
    member_id_str = str(member.id)

    if guild_id_str not in warnings_data:
        warnings_data[guild_id_str] = {}
    if member_id_str not in warnings_data[guild_id_str]:
        warnings_data[guild_id_str][member_id_str] = []

    warning_entry = {
        "reason": reason,
        "moderator": f"{ctx.author.name}#{ctx.author.discriminator}",
        "moderator_id": ctx.author.id,
        "timestamp": datetime.utcnow().isoformat()
    }
    warnings_data[guild_id_str][member_id_str].append(warning_entry)
    save_warnings() # Save warnings after modification

    warn_count = len(warnings_data[guild_id_str][member_id_str])

    embed = discord.Embed(
        title="⚠️ Member Warned",
        description=f"{member.mention} has been warned by {ctx.author.mention}.",
        color=discord.Color.yellow()
    )
    embed.add_field(name="Member", value=f"{member.name}#{member.discriminator} ({member.id})", inline=False)
    embed.add_field(name="Moderator", value=f"{ctx.author.name}#{ctx.author.discriminator} ({ctx.author.id})", inline=False)
    embed.add_field(name="Reason", value=reason, inline=False)
    embed.add_field(name="Total Warnings", value=warn_count, inline=False)
    embed.timestamp = datetime.utcnow()
    embed.set_footer(text=f"User ID: {member.id}")

    await ctx.send(f"✅ Warned {member.mention}. They now have {warn_count} warning(s).")
    await log_action(ctx.guild, embed)

    try:
        await member.send(f"You have been warned in {ctx.guild.name} for: `{reason}`. You now have {warn_count} warning(s).")
    except discord.Forbidden:
        pass # Couldn't DM the user

@bot.command(name='warnings', help="Shows a member's warnings.")
@commands.has_permissions(kick_members=True) # Or a custom role/permission check
@commands.bot_has_permissions(send_messages=True)
async def view_warnings(ctx, member: discord.Member):
    guild_id_str = str(ctx.guild.id)
    member_id_str = str(member.id)

    if guild_id_str not in warnings_data or member_id_str not in warnings_data[guild_id_str] or not warnings_data[guild_id_str][member_id_str]:
        return await ctx.send(f"{member.mention} has no warnings.")

    warnings = warnings_data[guild_id_str][member_id_str]

    embed = discord.Embed(
        title=f"Warnings for {member.name}#{member.discriminator}",
        description=f"Total Warnings: **{len(warnings)}**",
        color=discord.Color.blue()
    )
    embed.set_thumbnail(url=member.avatar.url if member.avatar else member.default_avatar.url)

    for i, warning in enumerate(warnings):
        timestamp = datetime.fromisoformat(warning['timestamp']).strftime("%Y-%m-%d %H:%M:%S UTC")
        embed.add_field(
            name=f"Warning #{i+1}",
            value=(
                f"**Reason:** {warning['reason']}\n"
                f"**Moderator:** {warning['moderator']} (<@{warning['moderator_id']}>)\n"
                f"**Date:** {timestamp}"
            ),
            inline=False
        )
    embed.set_footer(text=f"User ID: {member.id}")
    await ctx.send(embed=embed)

@bot.command(name='clearwarns', aliases=['removewarns'], help="Clears all warnings for a member.")
@commands.has_permissions(ban_members=True) # Higher permission for clearing all warnings
@commands.bot_has_permissions(send_messages=True)
async def clear_warnings(ctx, member: discord.Member):
    guild_id_str = str(ctx.guild.id)
    member_id_str = str(member.id)

    if guild_id_str not in warnings_data or member_id_str not in warnings_data[guild_id_str] or not warnings_data[guild_id_str][member_id_str]:
        return await ctx.send(f"{member.mention} has no warnings to clear.")

    old_warnings_count = len(warnings_data[guild_id_str][member_id_str])
    warnings_data[guild_id_str][member_id_str] = []
    save_warnings() # Save warnings after modification

    embed = discord.Embed(
        title="✅ Warnings Cleared",
        description=f"All {old_warnings_count} warnings for {member.mention} have been cleared by {ctx.author.mention}.",
        color=discord.Color.green()
    )
    embed.add_field(name="Member", value=f"{member.name}#{member.discriminator} ({member.id})", inline=False)
    embed.add_field(name="Moderator", value=f"{ctx.author.name}#{ctx.author.discriminator} ({ctx.author.id})", inline=False)
    embed.timestamp = datetime.utcnow()
    embed.set_footer(text=f"User ID: {member.id}")

    await ctx.send(f"✅ Cleared all {old_warnings_count} warnings for {member.mention}.")
    await log_action(ctx.guild, embed)


@bot.command(name='purge', aliases=['clear'], help='Deletes a specified number of messages. Usage: !purge <amount>')
@commands.has_permissions(manage_messages=True)
@commands.bot_has_permissions(manage_messages=True)
async def purge(ctx, amount: int):
    if amount <= 0:
        return await ctx.send("Please specify a positive number of messages to delete.")
    if amount > 100:
        return await ctx.send("You can only delete up to 100 messages at a time.")

    try:
        deleted = await ctx.channel.purge(limit=amount + 1) # +1 to also delete the command message
        embed = discord.Embed(
            title="🗑️ Messages Purged",
            description=f"**{len(deleted) - 1} messages** deleted in {ctx.channel.mention} by {ctx.author.mention}.",
            color=discord.Color.light_grey()
        )
        embed.add_field(name="Moderator", value=f"{ctx.author.name}#{ctx.author.discriminator} ({ctx.author.id})", inline=False)
        embed.add_field(name="Channel", value=ctx.channel.mention, inline=False)
        embed.add_field(name="Amount", value=f"{len(deleted) - 1}", inline=False)
        embed.timestamp = datetime.utcnow()
        await log_action(ctx.guild, embed)
        
        # Send confirmation in the same channel, then delete it after a few seconds
        confirm_msg = await ctx.send(f"✅ Deleted **{len(deleted) - 1}** messages.", delete_after=5)
        
    except discord.Forbidden:
        await ctx.send("I don't have permission to manage messages in this channel.")
    except Exception as e:
        await ctx.send(f"An error occurred: {e}")

# --- Utility Commands ---
@bot.command(name='userinfo', aliases=['whois', 'ui'], help='Displays information about a user.')
@commands.bot_has_permissions(send_messages=True)
async def userinfo(ctx, member: discord.Member = None):
    member = member or ctx.author # Defaults to the command author if no member is specified

    embed = discord.Embed(
        title=f"User Info: {member.name}#{member.discriminator}",
        color=member.color if member.color != discord.Color.default() else discord.Color.blue()
    )
    embed.set_thumbnail(url=member.avatar.url if member.avatar else member.display_avatar.url)
    embed.add_field(name="ID", value=member.id, inline=True)
    embed.add_field(name="Nickname", value=member.nick if member.nick else "None", inline=True)
    embed.add_field(name="Bot?", value="Yes" if member.bot else "No", inline=True)
    embed.add_field(name="Account Created", value=member.created_at.strftime("%Y-%m-%d %H:%M:%S UTC"), inline=False)
    embed.add_field(name="Joined Server", value=member.joined_at.strftime("%Y-%m-%d %H:%M:%S UTC") if member.joined_at else "N/A", inline=False)

    roles = [role.mention for role in member.roles if role != ctx.guild.default_role]
    if roles:
        embed.add_field(name=f"Roles ({len(roles)})", value=", ".join(roles), inline=False)
    else:
        embed.add_field(name="Roles", value="None", inline=False)

    warnings_count = 0
    guild_id_str = str(ctx.guild.id)
    member_id_str = str(member.id)
    if guild_id_str in warnings_data and member_id_str in warnings_data[guild_id_str]:
        warnings_count = len(warnings_data[guild_id_str][member_id_str])
    embed.add_field(name="Warnings", value=warnings_count, inline=True)
    
    embed.set_footer(text=f"Requested by {ctx.author.name}")
    embed.timestamp = datetime.utcnow()

    await ctx.send(embed=embed)

@bot.command(name='status', help='Displays bot uptime and status.')
@commands.bot_has_permissions(send_messages=True)
async def status(ctx):
    uptime = datetime.utcnow() - bot.start_time
    hours, remainder = divmod(int(uptime.total_seconds()), 3600)
    minutes, seconds = divmod(remainder, 60)
    
    embed = discord.Embed(
        title="Bot Status",
        color=discord.Color.green()
    )
    embed.add_field(name="Uptime", value=f"{hours}h {minutes}m {seconds}s", inline=False)
    embed.add_field(name="Latency", value=f"{round(bot.latency * 1000)}ms", inline=False)
    embed.add_field(name="Guilds", value=len(bot.guilds), inline=True)
    embed.add_field(name="Users", value=len(bot.users), inline=True)
    embed.timestamp = datetime.utcnow()
    embed.set_footer(text=f"Bot ID: {bot.user.id}")

    await ctx.send(embed=embed)


@bot.command(name='reloadconfig', help='Reloads the configuration from config.json (Owner only).')
@commands.is_owner()
async def reload_config_command(ctx):
    global config
    old_prefix = config['prefix']
    try:
        config = load_config()
        bot.command_prefix = config['prefix'] # Update the bot's prefix
        await ctx.send(f"✅ Configuration reloaded successfully. Prefix is now: `{config['prefix']}` (was `{old_prefix}`).")
    except Exception as e:
        await ctx.send(f"❌ Failed to reload configuration: `{e}`")

# --- Run the Bot ---
if TOKEN is None:
    print("Error: DISCORD_TOKEN not found in .env file.")
else:
    bot.run(TOKEN)