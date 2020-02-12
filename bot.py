from pytz import timezone
from datetime import datetime, timedelta
import discord
from discord.ext import commands
from discord.ext.commands import has_permissions
import asyncpg
import asyncio
import math
import os

class Bot(commands.Bot):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.pool = None
        self.config = {
            'bot_user_token': str(os.environ.get('BOT_USER_TOKEN')),
            'database_url': str(os.environ.get('DATABASE_URL')),
            'del_msg_after_secs': int(os.environ.get('DEL_MSG_AFTER_SECS')),
            'table_entry_expiration_mins': int(os.environ.get('TABLE_ENTRY_EXPIRATION_MINS')),
            'table_refresh_rate_secs': int(os.environ.get('TABLE_REFRESH_RATE_SECS')),
        }
        self.guild_state_map = {}
        self.stuff_loaded = False
        self.tz_str = 'Europe/Berlin'

bot = Bot(command_prefix='!')

extensions = (
    'Event',
    'Track',
    'Admin'
)
for extension in extensions:
    bot.load_extension('cog.' + extension.lower())

@bot.event
async def on_ready():
    bot.stuff_loaded = False
    print('Logged in as {0.user}!'.format(bot))
    print('Connected to {0} guild(s).'.format(len(bot.guilds)))
    for guild in bot.guilds:
        print(guild.name)
        guild_state = {
            'id_guild': guild.id,
            'talonro': True,
            'id_member_channel': None,
            'user_state_map': {},
            'channel_state_map': {}
        }
        bot.guild_state_map[guild.id] = guild_state
    bot.pool = await asyncpg.create_pool(dsn=bot.config['database_url'])
    conn = await bot.pool.acquire()
    try:
        meta_sql = 'SELECT * FROM pg_catalog.pg_tables '
        meta_sql += 'WHERE schemaname=\'public\' and tablename=\'mvp\''
        db_table_list = await conn.fetch(meta_sql)
        if len(db_table_list) == 0:
            sql_file = open('sql/yellowtracker.sql', 'r')
            await conn.execute(sql_file.read())
        db_guild_list = await conn.fetch('SELECT * FROM guild')
        for guild in bot.guilds:
            db_guild = next((x for x in db_guild_list if x['id'] == guild.id), None)
            if db_guild is None:
                await conn.execute('INSERT INTO guild(id)VALUES($1)', guild.id)
                continue
            guild_state = bot.guild_state_map[guild.id]
            guild_state['talonro'] = db_guild['talonro']
            guild_state['id_member_channel'] = db_guild['id_member_channel']
        for db_guild in db_guild_list:
            if next((x for x in bot.guilds if x.id == db_guild['id']), None) is None:
                await conn.execute('DELETE FROM mvp_guild WHERE id_guild=$1', db_guild['id'])
                await conn.execute('DELETE FROM mining_guild WHERE id_guild=$1', db_guild['id'])
                await conn.execute('DELETE FROM channel_guild WHERE id_guild=$1', db_guild['id'])
                await conn.execute('DELETE FROM guild WHERE id=$1', db_guild['id'])
        for guild in bot.guilds:
            db_channel_guild_list = await conn.fetch(
                'SELECT * FROM channel_guild where id_guild=$1',
                guild.id
            )
            for db_channel_guild in db_channel_guild_list:
                channel_state = dict(db_channel_guild)
                channel_state['id_message'] = None
                channel_state['entry_state_list'] = []
                guild_state['channel_state_map'][channel_state['id_channel']] = channel_state
    finally:
        await bot.pool.release(conn)
    bot.stuff_loaded = True

@bot.event
async def on_guild_join(guild):
    print('Joined to {0.name}!'.format(guild))
    guild_state = {
        'id_guild': guild.id,
        'talonro': True,
        'user_state_map': {},
        'channel_state_map': {}
    }
    bot.guild_state_map[guild.id] = guild_state
    conn = await bot.pool.acquire()
    try:
        read_sql = 'SELECT * FROM guild WHERE id=$1'
        guild_db = await conn.fetch(read_sql, guild.id)
        if len(guild_db) == 0:
            write_sql = 'INSERT INTO guild(id)VALUES($1)'
            await conn.execute(write_sql, guild.id)
    finally:
        await bot.pool.release(conn)

@bot.event
async def on_guild_remove(guild):
    print('Withdrawn from {0.name}.'.format(guild))
    bot.guild_state_map[guild.id] = None
    conn = await bot.pool.acquire()
    try:
        await conn.execute('DELETE FROM mvp_guild WHERE id_guild=$1', guild.id)
        await conn.execute('DELETE FROM mining_guild WHERE id_guild=$1', guild.id)
        await conn.execute('DELETE FROM channel_guild WHERE id_guild=$1', guild.id)
        await conn.execute('DELETE FROM guild WHERE id=$1', guild.id)
    finally:
        await bot.pool.release(conn)

async def timer_thread(bot):
    while True:
        for extension in extensions:
            cog = bot.get_cog(extension)
            if cog is not None and hasattr(cog, 'timer'):
                await cog.timer()
        await asyncio.sleep(bot.config['table_refresh_rate_secs'])

@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.errors.CommandNotFound):
        return
    elif isinstance(error, commands.errors.MissingPermissions):
        return
    elif isinstance(error, commands.errors.MissingRequiredArgument):
        return
    raise error

tasks = [
    asyncio.ensure_future(timer_thread(bot)),
    asyncio.ensure_future(bot.start(bot.config['bot_user_token']))
]
loop = asyncio.get_event_loop()
loop.run_until_complete(asyncio.gather(*tasks))