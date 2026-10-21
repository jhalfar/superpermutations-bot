"""discord_bot.py - Discord front end of superpermutations-bot v1.0.

Slash commands
  /check [attachment | url] [submit]   check a recipe (*.splan.json.xz), a SPRA word (*.spra, *.spra.xz) or a word (*.txt, *.txt.xz), n <= 14;
                                       without options it opens a form (file upload or link)
  /queue                               the two lanes: running jobs with progress, waiting jobs with positions (only you see it)
  /cancel job_id                       cancel your own job (Manage Messages or Administrator: any job)
  /records                             the table of shortest known words per n, with a select for the details of one n
  /about                               what the bot does and does not do, limits, required permissions
  /panel                               (Manage Server) posts a message with a "Check a word" button that opens the form

Run:   set DISCORD_TOKEN=...   (environment only; the token is never read from a file and never written)
       optional: SUPERPERM_CHANNEL, SUPERPERM_CONTRIBUTOR_ROLE, SUPERPERM_MEM_GB, SUPERPERM_MAX_N, SUPERPERM_MBPS,
       SUPERPERM_MAX_HOURS, SUPERPERM_QUEUE, SUPERPERM_MAX_SHARDS, SUPERPERM_RECORDS   (see README.md)
       python -I bot/discord_bot.py      (from the repository root; scripts/run_bot.ps1, scripts/run_bot.sh read .env)

Every accepted /check gets ONE card, a normal channel message sent with the bot token (channel.send), so it can be edited for
hours (an interaction token lives 15 minutes). The card shows the queue position, then a progress bar (edited every
progress_s seconds, never more than once per min_edit_s per message), then the result; its buttons are Cancel, Add to records
and Details. The person who started the check gets a short ephemeral reply. All cards are built by the pure functions of
ui.py. Checks run in two lanes (fast: n <= 12, slow: n = 13, 14), one worker each, in a separate process (checker/check_any.py) under
a memory limit (limits.py). The state is in memory: a restart drops the queue (buttons of old cards then say so).
Nothing a user sends is ever executed: a file is data for the fixed decoders of registry.py.
The handlers use only a few attributes of the interaction, so they run against fake objects in tests/test_bot.py.
Every exception that is caught and not shown to the user is logged with logging.warning (logger "superperm").

Modules (all in bot/; this file only builds the client and runs it):
  config.py     Config (the SUPERPERM_* environment), constants, UserError
  downloads.py  links: allowed hosts, redirects, size cap, deadlines; file names and extensions
  jobs.py       plan of a job (limits, refusals), Job, the two lanes (Scheduler), the worker (supervise, check_any.py)
  state.py      State and JobCtx: everything the bot keeps in memory
  cards.py      the job card (Card), its refresh, replies, display name and permissions
  commands.py   the handlers: /check and its form, /queue, /cancel, /records, /about, /settings, /panel, job buttons
  submit.py     Add to records / submit: the table, the Contributor role, the announcement
  manage.py     /history, rename and delete of history and records entries, the audit lines
  ui.py         the cards as pure functions; records.py, settings.py, history.py, magic.py
"""
import asyncio
import os
import sys
from typing import Literal

import os as _os, sys as _sys
_sys.path.insert(0, _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..", "checker"))  # check_any, registry, ...
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))  # python -I drops the script folder
import discord

import history
import ui
from commands import (CheckModal, handle_about, handle_cancel, handle_check, handle_job_button,  # noqa: F401
                      handle_panel, handle_panel_button, handle_queue, handle_records, handle_records_detail,
                      handle_settings, handle_settings_set)
from manage import RenameModal, handle_history, handle_rename, handle_x, hist_try, history_loop  # noqa: F401
from state import State

# re-exported: the tests and tools/preview_cards.py use them as discord_bot.NAME
from cards import Card, refresh_interval  # noqa: F401,E402
from config import VERSION, Config, UserError  # noqa: F401,E402
from downloads import clean_name, download, ext_of, validate_url  # noqa: F401,E402
from jobs import Job, Plan, Scheduler, plan_job, subprocess_runner, supervise  # noqa: F401,E402
from state import JobCtx  # noqa: F401,E402
from submit import do_submit, handle_add  # noqa: F401,E402
from ui import fmt_dur, fmt_letters  # noqa: F401,E402


def build_client(state):
    from discord import app_commands

    ui.HANDLERS["job"] = lambda act, jid, interaction: handle_job_button(state, act, jid, interaction)
    ui.HANDLERS["panel"] = lambda interaction: handle_panel_button(state, interaction)
    ui.HANDLERS["records"] = lambda n, interaction: handle_records_detail(state, n, interaction)
    ui.HANDLERS["settings"] = lambda value, interaction: handle_settings_set(state, value, interaction)
    ui.HANDLERS["x"] = lambda kind, arg, value, interaction: handle_x(state, kind, arg, value, interaction)
    where = app_commands.allowed_contexts(guilds=True, dms=True, private_channels=False)      # also in a DM with the bot

    class Client(discord.Client):
        def __init__(self):
            super().__init__(intents=discord.Intents.default())     # no privileged intents
            self.tree = app_commands.CommandTree(self)

        async def setup_hook(self):
            state.sched.start()
            self.add_dynamic_items(ui.JobButton, ui.PanelButton, ui.RecordsSelect, ui.SettingsSelect, ui.XButton, ui.XSelect)  # survive a restart
            await asyncio.to_thread(hist_try, "purge", history.purge, state.cfg.history_path, state.cfg.history_days)
            state.history_task = asyncio.ensure_future(history_loop(state))
            if state.cfg.audit_channel_id:
                async def audit_channel():
                    return self.get_channel(state.cfg.audit_channel_id) or await self.fetch_channel(state.cfg.audit_channel_id)
                state.audit_channel = audit_channel
            if state.cfg.channel_id:
                async def find_member(user_id):
                    ch = self.get_channel(state.cfg.channel_id) or await self.fetch_channel(state.cfg.channel_id)
                    return await ch.guild.fetch_member(user_id)
                state.find_member = find_member
            if state.cfg.channel_id:
                async def announce(view):
                    ch = self.get_channel(state.cfg.channel_id) or await self.fetch_channel(state.cfg.channel_id)
                    await ch.send(view=view, allowed_mentions=discord.AllowedMentions.none())
                state.announce = announce
            await self.tree.sync()

    client = Client()
    tree = client.tree

    @tree.command(name="check", description="Check a recipe or a word for all permutations (n <= %d); no options: a form" %
                  state.cfg.max_n)
    @app_commands.describe(attachment="a .splan.json.xz recipe, .spra or .spra.xz word, .txt or .txt.xz word",
                           url="public file on github.com, raw.githubusercontent.com or gitlab.com",
                           submit="enter a valid word into the records table",
                           visibility="public: the card is posted here; private: it comes by direct message (default: /settings)")
    @where
    async def check(interaction: discord.Interaction, attachment: discord.Attachment = None, url: str = None,
                    submit: bool = False, visibility: Literal["public", "private"] = None):
        await handle_check(state, interaction, attachment, url, submit, visibility=visibility)

    @tree.command(name="history", description="Your recent checks (moderators: another user's)")
    @app_commands.describe(user="moderators only: show this user's checks")
    @where
    async def history_cmd(interaction: discord.Interaction, user: discord.User = None):
        await handle_history(state, interaction, user)

    @tree.command(name="settings", description="Your default: private (card by direct message) or public checks")
    @where
    async def settings_cmd(interaction: discord.Interaction):
        await handle_settings(state, interaction)

    @tree.command(name="queue", description="The two lanes: running and waiting checks")
    @where
    async def queue_cmd(interaction: discord.Interaction):
        await handle_queue(state, interaction)

    @tree.command(name="cancel", description="Cancel your check (staff: any check) by job number")
    @app_commands.describe(job_id="the number after # in the card title (Check #12)")
    @where
    async def cancel_cmd(interaction: discord.Interaction, job_id: int):
        await handle_cancel(state, interaction, job_id)

    @tree.command(name="records", description="Shortest known words per n")
    @where
    async def records_cmd(interaction: discord.Interaction):
        await handle_records(state, interaction)

    @tree.command(name="about", description="What this bot does")
    @where
    async def about(interaction: discord.Interaction):
        await handle_about(state, interaction)

    @tree.command(name="panel", description="Post a message with a Check a word button in this channel (Manage Server)")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    async def panel(interaction: discord.Interaction):
        await handle_panel(state, interaction)

    return client


def main():
    token = os.environ.get("DISCORD_TOKEN")
    if not token:
        sys.exit("DISCORD_TOKEN is not set (environment variable only; nothing is read from files)")
    state = State()
    build_client(state).run(token)


if __name__ == "__main__":
    main()
