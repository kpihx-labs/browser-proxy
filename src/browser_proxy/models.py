"""Define the stable JSON models shared by CLI, daemon, and extension bridge.

Examples:
    >>> Envelope.ok({'profiles': []}).model_dump()['meta']['status']
    'ok'
    >>> Envelope.error('CDP_UNAVAILABLE').model_dump()['data']['code']
    'CDP_UNAVAILABLE'
"""

from typing import Any, Literal

from pydantic import BaseModel, Field


class Meta(BaseModel):
    """Describe non-business command execution metadata.

    Args:
        status: Final machine-readable outcome.
        comment: Optional human review comment.
        edited: Whether a reviewer edited the payload.

    Returns:
        A serializable metadata object.

    Examples:
        >>> Meta().status
        'ok'
        >>> Meta(status='approved', comment='Proceed').model_dump()['edited']
        False
    """

    status: Literal["ok", "approved", "rejected", "error"] = "ok"
    comment: str = ""
    edited: bool = False


class Envelope(BaseModel):
    """Wrap every browser-proxy response in the common proxy envelope.

    Args:
        meta: Command state and review information.
        data: Domain result or structured error payload.

    Returns:
        A JSON-safe response envelope.

    Examples:
        >>> Envelope.ok({'tabs': 2}).model_dump()['data']['tabs']
        2
        >>> Envelope.error('LEASE_CONFLICT').meta.status
        'error'
    """

    meta: Meta = Field(default_factory=Meta)
    data: Any = None

    @classmethod
    def ok(cls, data: Any, *, comment: str = "", edited: bool = False) -> "Envelope":
        """Build a successful envelope.

        Args:
            data: Action-specific response data.
            comment: Optional approved-review comment.
            edited: Whether an approval changed the request.

        Returns:
            An envelope whose metadata has status ``ok``.

        Examples:
            >>> Envelope.ok({'window_id': 5}).data['window_id']
            5
            >>> Envelope.ok([], edited=True).meta.edited
            True
        """

        return cls(meta=Meta(comment=comment, edited=edited), data=data)

    @classmethod
    def error(cls, code: str, *, message: str = "") -> "Envelope":
        """Build a structured failure envelope.

        Args:
            code: Stable application error code.
            message: Human-readable diagnostic with no secret content.

        Returns:
            An envelope with ``error`` status and error data.

        Examples:
            >>> Envelope.error('CDP_UNAVAILABLE').data['code']
            'CDP_UNAVAILABLE'
            >>> Envelope.error('BAD', message='bad payload').meta.status
            'error'
        """

        return cls(meta=Meta(status="error"), data={"code": code, "message": message})


class RpcRequest(BaseModel):
    """Represent a single local daemon request.

    Args:
        id: Client correlation identifier.
        method: Daemon method name.
        params: JSON object passed to the method.

    Returns:
        A validated request record.

    Examples:
        >>> RpcRequest(id='1', method='ping').method
        'ping'
        >>> RpcRequest(id='2', method='do', params={'action': 'profile-list'}).id
        '2'
    """

    id: str
    method: str
    params: dict[str, Any] = Field(default_factory=dict)
