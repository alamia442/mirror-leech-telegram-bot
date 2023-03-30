#!/usr/bin/env python3
from pyrogram.handlers import MessageHandler, EditedMessageHandler
from pyrogram.filters import command
from io import BytesIO

from bot import LOGGER, bot
from bot.helper.telegram_helper.message_utils import editMessage, sendMessage, sendFile
from bot.helper.ext_utils.bot_utils import cmd_exec, new_task
from bot.helper.telegram_helper.filters import CustomFilters
from bot.helper.telegram_helper.bot_commands import BotCommands
from getpass import getuser
from os import geteuid, setsid, getpgid, killpg
from signal import SIGKILL
import asyncio


@new_task
async def shell(client, message):
    msg = await sendMessage(message, "<pre>Executing terminal ...</pre>")
    cmd = message.text.split(maxsplit=1)
    if len(cmd) == 1:
        await editMessage(msg, 'No command to execute was given.')
        return
    cmd = cmd[1]
    try:
        t_obj = await Terminal.execute(cmd)  # type: Term
    except Exception as t_e:  # pylint: disable=broad-except
        await editMessage(msg, str(t_e))
        return
    
    cur_user = getuser()
    uid = geteuid()

    prefix = f"<b>{cur_user}:~#</b>" if uid == 0 else f"<b>{cur_user}:~$</b>"
    output = f"{prefix} <pre>{cmd}</pre>\n"

#    with message.cancel_callback(t_obj.cancel):
    await t_obj.init()
    while not t_obj.finished:
        await editMessage(msg, f"{prefix}\n<pre>{t_obj.line}</pre>")
        await t_obj.wait(6)
 #       if t_obj.cancelled:
 #           await message.canceled(reply=True)
 #           return

    out_data = f"{output}<pre>{t_obj.output}</pre>\n"

    if len(out_data) > 4096:
        with BytesIO(str.encode(out_data)) as out_file:
            out_file.name = "shell_output.txt"
            await sendFile(message, out_file)
    else:
        await editMessage(message, out_data)
    del out_data

class Terminal:
    """ live update term class """

    def __init__(self, process: asyncio.subprocess.Process) -> None:
        self._process = process
        self._line = b''
        self._output = b''
        self._init = asyncio.Event()
        self._is_init = False
        self._cancelled = False
        self._finished = False
        self._loop = asyncio.get_running_loop()
        self._listener = self._loop.create_future()

    @property
    def line(self) -> str:
        return self._by_to_str(self._line)
    
    @property
    def output(self) -> str:
        return self._by_to_str(self._output)

    @staticmethod
    def _by_to_str(data: bytes) -> str:
        return data.decode('utf-8', 'replace').rstrip()

    @property
    def cancelled(self) -> bool:
        return self._cancelled

    @property
    def finished(self) -> bool:
        return self._finished

    async def init(self) -> None:
        await self._init.wait()

    async def wait(self, timeout: int) -> None:
        self._check_listener()
        try:
            await asyncio.wait_for(self._listener, timeout)
        except asyncio.TimeoutError:
            pass

    def _check_listener(self) -> None:
        if self._listener.done():
            self._listener = self._loop.create_future()

    def cancel(self) -> None:
        if self._cancelled or self._finished:
            return
        killpg(getpgid(self._process.pid), SIGKILL)
        self._cancelled = True

    @classmethod
    async def execute(cls, cmd: str) -> 'Terminal':
        kwargs = dict(
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE)
        if setsid:
            kwargs['preexec_fn'] = setsid
        process = await asyncio.create_subprocess_shell(cmd, **kwargs)
        t_obj = cls(process)
        t_obj._start()
        return t_obj

    def _start(self) -> None:
        self._loop.create_task(self._worker())

    async def _worker(self) -> None:
        if self._cancelled or self._finished:
            return
        await asyncio.wait([self._read_stdout(), self._read_stderr()])
        await self._process.wait()
        self._finish()

    async def _read_stdout(self) -> None:
        await self._read(self._process.stdout)

    async def _read_stderr(self) -> None:
        await self._read(self._process.stderr)

    async def _read(self, reader: asyncio.StreamReader) -> None:
        while True:
            line = await reader.read(n=1024)
            if not line:
                break
            self._append(line)

    def _append(self, line: bytes) -> None:
        self._line = line
        self._output += line
        self._check_init()

    def _check_init(self) -> None:
        if self._is_init:
            return
        self._loop.call_later(1, self._init.set)
        self._is_init = True

    def _finish(self) -> None:
        if self._finished:
            return
        self._init.set()
        self._finished = True
        if not self._listener.done():
            self._listener.set_result(None)

bot.add_handler(MessageHandler(shell, filters=command(BotCommands.ShellCommand) & CustomFilters.owner))
bot.add_handler(EditedMessageHandler(shell, filters=command(BotCommands.ShellCommand) & CustomFilters.owner))
