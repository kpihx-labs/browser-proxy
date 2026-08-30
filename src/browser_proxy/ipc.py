"""Length-prefixed message framing shared by the CLI client and the daemon.

Every request/response used to be one JSON line terminated by ``\\n``, read via
``StreamReader.readline()`` — bounded by asyncio's internal ~64 KiB line buffer
(``asyncio.LimitOverrunError``, surfaced to callers as the raw wire message ``Separator is found,
but chunk is longer than limit``). A genuinely large single-page CDP result (e.g.
``page-snapshot``'s full accessibility tree) routinely raced past that ceiling before the CLI's
own preview/autosave layer (``cli._emit_do``) ever got a chance to run, so the failure was a
transport bug, not something any individual action could work around.

This module replaces line framing with an explicit 8-byte big-endian length prefix followed by
exactly that many raw bytes — no embedded separator to overrun, so message size is bounded ONLY by
the configured ``config.IPC_MAX_MESSAGE_BYTES_DEFAULT`` ceiling, never by a line-buffer limit.
``client.request`` and ``daemon.Daemon._handle`` both call ``write_message``/``read_message``
exclusively — neither ever touches ``readline``/raw ``write`` framing directly again.

Examples:
    >>> max_message_bytes() > 0
    True
    >>> read_message.__name__
    'read_message'
"""

import asyncio
import os
import struct

from browser_proxy import config

_LENGTH_PREFIX_FORMAT = "!Q"
_LENGTH_PREFIX_SIZE = struct.calcsize(_LENGTH_PREFIX_FORMAT)


def max_message_bytes() -> int:
    """Purpose: resolve the configured IPC message ceiling at call time (env-overridable).

    Args:
        None: Reads the optional ``BROWSER_PROXY_IPC_MAX_MESSAGE_BYTES`` environment value.

    Returns:
        int: The maximum allowed single-message body size in bytes, always at least 1 so a
        misconfigured override can never silently disable the bound entirely.

    Examples:
        >>> max_message_bytes() > 0
        True
        >>> isinstance(max_message_bytes(), int)
        True
    """

    raw = os.environ.get(
        config.ENV_IPC_MAX_MESSAGE_BYTES, str(config.IPC_MAX_MESSAGE_BYTES_DEFAULT)
    )
    return max(1, int(raw))


async def write_message(writer: asyncio.StreamWriter, payload: bytes) -> None:
    """Purpose: frame and flush one message with an explicit 8-byte length prefix.

    Args:
        writer (asyncio.StreamWriter): Connected stream the framed message is written to.
        payload (bytes): Complete message body, of any size up to the configured ceiling — the
            write side trusts its own local process and never enforces the read-side bound.

    Returns:
        None: The prefix and payload are written and flushed before returning.

    Examples:
        >>> write_message.__name__
        'write_message'
        >>> asyncio.iscoroutinefunction(write_message)
        True
    """

    writer.write(struct.pack(_LENGTH_PREFIX_FORMAT, len(payload)))
    writer.write(payload)
    await writer.drain()


async def read_message(reader: asyncio.StreamReader) -> bytes:
    """Purpose: read one length-prefixed message, bounded by the configured ceiling.

    Args:
        reader (asyncio.StreamReader): Connected stream the framed message is read from.

    Returns:
        bytes: The exact message body announced by the 8-byte length prefix.

    Raises:
        ConnectionError: The peer closed the connection before a full prefix or body arrived.
        ValueError: The announced length exceeds ``max_message_bytes()`` — rejected BEFORE reading
            the body, so a hostile or corrupt prefix never forces an unbounded read/allocation.

    Examples:
        >>> read_message.__name__
        'read_message'
        >>> asyncio.iscoroutinefunction(read_message)
        True
    """

    try:
        header = await reader.readexactly(_LENGTH_PREFIX_SIZE)
    except asyncio.IncompleteReadError as error:
        raise ConnectionError(
            "IPC_ERROR: connection closed before a length prefix arrived"
        ) from error
    (length,) = struct.unpack(_LENGTH_PREFIX_FORMAT, header)
    limit = max_message_bytes()
    if length > limit:
        raise ValueError(f"IPC_ERROR: message of {length} bytes exceeds the {limit}-byte limit")
    try:
        return await reader.readexactly(length)
    except asyncio.IncompleteReadError as error:
        raise ConnectionError(
            "IPC_ERROR: connection closed before the full message arrived"
        ) from error
