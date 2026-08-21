"""Centralize declarative policy metadata for browser actions.

Examples:
    >>> require_approval(lambda: None).__browser_policy__.approval
    True
    >>> require_verification('url')(lambda: None).__browser_policy__.verification
    ('url',)
"""

from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Any, TypeVar

F = TypeVar("F", bound=Callable[..., Any])


@dataclass(frozen=True)
class Policy:
    """Declare safety requirements attached to exactly one action handler.

    Args:
        approval: Whether the daemon must obtain explicit human approval.
        preflight_fields: Immutable identity fields read before mutation.
        verification: Fields that must match the read-back result.

    Returns:
        Immutable action policy metadata.

    Examples:
        >>> Policy(approval=True).approval
        True
        >>> Policy(verification=('url',)).verification
        ('url',)
    """

    approval: bool = False
    preflight_fields: tuple[str, ...] = ()
    verification: tuple[str, ...] = ()


def _with_policy(handler: F, policy: Policy) -> F:
    """Purpose: attach policy metadata without changing a handler signature.

    Args:
        handler (F): Action implementation receiving a validated payload.
        policy (Policy): Complete immutable policy to attach.

    Returns:
        The original handler with ``__browser_policy__`` metadata.

    Examples:
        >>> fn = _with_policy(lambda: None, Policy(approval=True))
        >>> fn.__browser_policy__.approval
        True
    """

    setattr(handler, "__browser_policy__", policy)
    return handler


def policy_of(handler: Callable[..., Any]) -> Policy:
    """Purpose: read existing metadata or return the safe default policy.

    Args:
        handler (Callable[..., Any]): Action function whose metadata is inspected.

    Returns:
        Existing policy or an empty policy.

    Examples:
        >>> policy_of(lambda: None).approval
        False
        >>> policy_of(_with_policy(lambda: None, Policy(approval=True))).approval
        True
    """

    return getattr(handler, "__browser_policy__", Policy())


def require_approval(handler: F) -> F:
    """Purpose: mark an action as requiring an extension-mediated human decision.

    Args:
        handler (F): Action implementation to protect.

    Returns:
        The same handler annotated with an approval policy.

    Examples:
        >>> require_approval(lambda: None).__browser_policy__.approval
        True
        >>> require_approval(lambda: None).__name__
        '<lambda>'
    """

    return _with_policy(handler, replace(policy_of(handler), approval=True))


def require_preflight(*identity_fields: str) -> Callable[[F], F]:
    """Purpose: protect mutation identity with daemon preflight fields.

    Args:
        identity_fields (str): Payload names identifying an existing browser resource.

    Returns:
        A decorator that adds immutable identity requirements.

    Examples:
        >>> require_preflight('tab_id')(lambda: None).__browser_policy__.preflight_fields
        ('tab_id',)
        >>> require_preflight('profile', 'window_id')(lambda: None).__browser_policy__.preflight_fields
        ('profile', 'window_id')
    """

    def decorate(handler: F) -> F:
        """Purpose: apply the declared immutable preflight fields to one handler.

        Args:
            handler (F): Action implementation receiving the augmented policy.

        Returns:
            F: The same handler with immutable preflight metadata.

        Examples:
            >>> decorate(lambda: None).__browser_policy__.preflight_fields
            identity_fields
            >>> callable(decorate)
            True
        """
        return _with_policy(handler, replace(policy_of(handler), preflight_fields=identity_fields))

    return decorate


def require_verification(*fields: str) -> Callable[[F], F]:
    """Purpose: require post-action read-back checks for declared result fields.

    Args:
        fields (str): Expected payload/result fields to validate after a mutation.

    Returns:
        A decorator that adds verification requirements.

    Examples:
        >>> require_verification('url')(lambda: None).__browser_policy__.verification
        ('url',)
        >>> require_verification('title', 'url')(lambda: None).__browser_policy__.verification
        ('title', 'url')
    """

    def decorate(handler: F) -> F:
        """Purpose: apply the declared verification fields to one handler.

        Args:
            handler (F): Action implementation receiving the augmented policy.

        Returns:
            F: The same handler with verification metadata.

        Examples:
            >>> decorate(lambda: None).__browser_policy__.verification
            fields
            >>> callable(decorate)
            True
        """
        return _with_policy(handler, replace(policy_of(handler), verification=fields))

    return decorate
