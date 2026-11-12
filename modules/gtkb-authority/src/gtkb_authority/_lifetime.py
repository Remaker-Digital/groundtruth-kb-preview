"""One original operation and connection owner. Copyright 2026 Remaker Digital."""
from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, replace
from functools import wraps
import math
import logging
import os
from pathlib import Path
import re
import stat
import threading
import uuid
from types import MappingProxyType
from typing import Any

import psycopg
from gtkb_protocol import ProtocolError

from .settings import AuthorityOperation, OperationProvider, ParametersProvider, PostgresSettings
from ._deadlines import expired

_CONFIG_MESSAGE = 'Explicit PostgreSQL configuration is invalid; correct the selected protected binding before retrying'
_OUTAGE_MESSAGE = 'PostgreSQL service is unavailable; restore the selected protected binding before retrying'
_REQUIRED = frozenset({'host', 'hostaddr', 'port', 'dbname', 'user', 'password', 'sslmode'})
_ALLOWED = _REQUIRED | {'sslrootcert', 'sslcrl'}


def _config(stage: str, reason: str, field: str | None = None) -> None:
    details = {'stage': stage, 'reason': reason}
    if field in _ALLOWED:
        details['field'] = field
    raise ProtocolError('invalid_postgresql_config', _CONFIG_MESSAGE, details=details)


def _ambient(*, tls: bool = False) -> None:
    # Names alone are inspected; no competing private value or profile is read.
    forbidden = {'OPENSSL_CONF', 'OPENSSL_MODULES', 'SSL_CERT_FILE', 'SSL_CERT_DIR'} if tls else set()
    if any(name.upper().startswith('PG') or name.upper() in forbidden for name in os.environ):
        _config('environment', 'ambient_selector')


def _ordinary_binding_path(value: Any, root: Path, *, absent_leaf: bool) -> str:
    if not isinstance(value, str) or not value or '~' in value:
        _config('transport', 'invalid_value')
    p = Path(value)
    if not p.is_absolute() or '..' in p.parts:
        _config('transport', 'invalid_value')
    try:
        p.relative_to(root)
        for current in [root, *reversed(p.parents), p]:
            if not current.is_relative_to(root):
                continue
            try:
                info = current.lstat()
            except FileNotFoundError:
                if current == p and absent_leaf:
                    continue
                raise
            if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
                _config('transport', 'invalid_value')
            if current == p and not stat.S_ISREG(info.st_mode):
                _config('transport', 'invalid_value')
            if current != p and not stat.S_ISDIR(info.st_mode):
                _config('transport', 'invalid_value')
        if p.resolve() != p:
            _config('transport', 'invalid_value')
    except (OSError, RuntimeError, ValueError):
        _config('transport', 'invalid_value')
    return str(p)


def _parameters(provider: ParametersProvider, settings: PostgresSettings, root: Path) -> Mapping[str, str | int]:
    if not callable(provider):
        _config('binding', 'missing_provider')
    try:
        supplied = provider(settings.service)
    except ProtocolError as error:
        if error.code == 'invalid_postgresql_config' and error.details.get('reason') == 'service_mismatch':
            _config('binding', 'service_mismatch')
        raise ProtocolError('postgres_unavailable', _OUTAGE_MESSAGE, details={'stage': 'binding', 'reason': 'unavailable'}) from None
    except Exception as error:
        logging.getLogger(__name__).error('binding provider failure incident=%s type=%s', uuid.uuid4().hex, type(error).__name__)
        raise ProtocolError('postgres_unavailable', _OUTAGE_MESSAGE, details={'stage': 'binding', 'reason': 'unavailable'}) from None
    try:
        if not isinstance(supplied, Mapping):
            _config('parameters', 'invalid_result')
        values = dict(supplied)
    except ProtocolError:
        raise
    except Exception:
        _config('parameters', 'invalid_result')
    if set(values) - _ALLOWED:
        _config('parameters', 'unknown_key')
    if _REQUIRED - values.keys():
        _config('parameters', 'missing_key')
    for field in ['host', 'hostaddr']:
        if values[field] != '127.0.0.1':
            _config('parameters', 'invalid_value', field)
    if type(values['port']) is not int or not 1 <= values['port'] <= 65535:
        _config('parameters', 'invalid_value', 'port')
    for field in ['dbname', 'user']:
        value = values[field]
        if not isinstance(value, str) or not value or any(ord(c) < 32 or ord(c) == 127 for c in value):
            _config('parameters', 'invalid_value', field)
    name = values['dbname']
    if '=' in name or re.match(r'^[A-Za-z][A-Za-z0-9+.-]*:', name):
        _config('parameters', 'invalid_value', 'dbname')
    password = values['password']
    if not isinstance(password, str) or not password or '\x00' in password:
        _config('parameters', 'invalid_value', 'password')
    mode = values['sslmode']
    if not isinstance(mode, str) or mode not in {'disable', 'verify-full'}:
        _config('transport', 'invalid_value', 'sslmode')
    if mode == 'disable':
        if set(values) != _REQUIRED:
            _config('transport', 'invalid_value')
    else:
        _ambient(tls=True)
        if not {'sslrootcert', 'sslcrl'} <= values.keys():
            _config('transport', 'missing_key')
        values['sslrootcert'] = _ordinary_binding_path(values['sslrootcert'], root, absent_leaf=False)
        values['sslcrl'] = _ordinary_binding_path(values['sslcrl'], root, absent_leaf=True)
    if psycopg.pq.version() < 170000:
        _config('transport', 'unsupported_transport')
    values.update(connect_timeout=0, options='', gssencmode='disable', sslcertmode='disable', require_auth='scram-sha-256')
    return MappingProxyType(values)


def _operation(provider: OperationProvider) -> AuthorityOperation:
    if not callable(provider):
        raise ProtocolError('operational_policy_unavailable', 'The operational policy is unavailable')
    try:
        op = provider()
    except Exception:
        raise ProtocolError('operational_policy_unavailable', 'The operational policy is unavailable') from None
    if op is None:
        raise ProtocolError('operational_policy_unavailable', 'The operational policy is unavailable')
    valid = type(op) is AuthorityOperation
    if valid:
        valid = isinstance(op.source_id, str) and bool(op.source_id.strip())
        valid = valid and isinstance(op.snapshot_id, str) and re.fullmatch('sha256:[0-9a-f]{64}', op.snapshot_id) is not None
        valid = valid and all(type(v) is int and 0 <= v <= 2147483647 for v in [op.lock_timeout_ms, op.statement_timeout_ms])
        for value in [op.connect_poll_seconds, op.repository_probe_seconds]:
            try:
                valid = valid and type(value) in (int, float) and math.isfinite(value) and value > 0
            except (OverflowError, TypeError, ValueError):
                valid = False
        valid = valid and type(op.read_page_size) is int and 1 <= op.read_page_size <= 1000
        try:
            valid = valid and (op.deadline is None or (type(op.deadline) in (int, float) and math.isfinite(op.deadline)))
        except (OverflowError, TypeError, ValueError):
            valid = False
        valid = valid and callable(op.checkpoint)
        if op.lock_timeout_ms == 0 or op.statement_timeout_ms == 0:
            valid = valid and isinstance(op.disabled_reason, str) and bool(op.disabled_reason.strip())
        else:
            valid = valid and op.disabled_reason is None
    if not valid:
        raise ProtocolError('invalid_operational_policy', 'The operational policy is invalid')
    return replace(op)


@dataclass(repr=False)
class _Frame:
    policy: AuthorityOperation
    parameters: Mapping[str, str | int]
    consumer: threading.Thread


class _Connection:
    """Keep the original connection and any exceptional context ending owned."""
    def __init__(self, owner: OperationOwner, original: Any) -> None:
        self._owner, self._original = owner, original

    def __getattr__(self, name: str) -> Any:
        return getattr(self._original, name)

    def __enter__(self) -> _Connection:
        self._original.__enter__()
        return self

    def __exit__(self, *args: Any) -> None:
        # This constructor owns direct nonpool Psycopg connections. Use its public
        # commit/rollback recipe and observe our one original finish directly.
        body_primary = args[1]
        primary = body_primary
        committed = False
        try:
            if not self._original.closed:
                if body_primary is None:
                    self._original.commit()
                    committed = True
                else:
                    self._original.rollback()
        except BaseException as error:
            self._owner._retain_connection_failure(self._original, error)
            if isinstance(error, Exception) and body_primary is not None:
                body_primary.add_note('AUTH transaction cleanup failed (' + type(error).__name__ + ')')
            else:
                primary = error
        try:
            self._owner._finish_original(self._original)
        except Exception as cleanup:
            if primary is not None:
                primary.add_note('AUTH connection cleanup failed (' + type(cleanup).__name__ + ')')
                raise primary from primary.__cause__
            if committed:
                raise ProtocolError(
                    'authority_ending_unresolved',
                    'The transaction committed; original connection ending is unresolved',
                    details={'transaction_outcome': 'committed',
                             'recovery': 'read_current_and_history_before_retrying'},
                ) from cleanup
            raise
        except BaseException:
            # Retention occurred in _finish_original. Never suppress a new cancel.
            raise
        if primary is not None and primary is not body_primary:
            raise primary
        # Returning None preserves the context body's original exception unchanged.


class OperationOwner:
    def _initialize_owner(self, settings: PostgresSettings, root: Path, parameters_provider: ParametersProvider, operation_provider: OperationProvider) -> None:
        if type(settings) is not PostgresSettings or not isinstance(settings.service, str) or re.fullmatch(r'[A-Za-z0-9_.-]+', settings.service) is None:
            _config('binding', 'invalid_value')
        if not isinstance(root, Path) or not root.is_absolute():
            _config('binding', 'invalid_value')
        self.settings = settings
        self._project_root = root
        self._parameters_provider, self._operation_provider = parameters_provider, operation_provider
        self._owner_lock = threading.RLock()
        self._owner_state = 'open'
        self._active_operations = 0
        self._operation_local = threading.local()
        self._connections: dict[int, Any] = {}
        self._connection_failures: dict[int, list[BaseException]] = {}
        self._service_owner: Any = None

    def _claim_service_owner(self, service: Any) -> None:
        with self._owner_lock:
            if self._service_owner is not None:
                raise ProtocolError('authority_owner_conflict', 'The kernel already has a service lifetime owner')
            self._check_admission()
            self._service_owner = service

    def _check_admission(self) -> None:
        if self._owner_state != 'open':
            raise ProtocolError('authority_' + self._owner_state, 'Authority admission is ' + self._owner_state)

    @contextmanager
    def operation_scope(self) -> Iterator[None]:
        frame = getattr(self._operation_local, 'frame', None)
        if frame is not None:
            if frame.consumer is not threading.current_thread():
                raise ProtocolError('postgres_connection_scope_changed', 'Use the original operation scope')
            yield
            return
        with self._owner_lock:
            self._check_admission()
            self._active_operations += 1
        try:
            _ambient()
            policy = _operation(self._operation_provider)
            policy.checkpoint()
            if expired(policy.deadline):
                raise ProtocolError('postgres_connection_deadline_expired', 'The original PostgreSQL operation expired')
            parameters = _parameters(self._parameters_provider, self.settings, self._project_root)
            frame = _Frame(policy, parameters, threading.current_thread())
            self._operation_local.frame = frame
            policy.checkpoint()
            yield
        finally:
            self._operation_local.frame = None
            with self._owner_lock:
                self._active_operations -= 1

    def _operation_policy(self) -> AuthorityOperation:
        frame = getattr(self._operation_local, 'frame', None)
        if frame is None or frame.consumer is not threading.current_thread():
            raise ProtocolError('operational_policy_unavailable', 'Use the original admitted operation')
        return frame.policy

    def _operation_parameters(self) -> Mapping[str, str | int]:
        self._operation_policy()
        return self._operation_local.frame.parameters

    def _own_connection(self, original: Any) -> _Connection:
        with self._owner_lock:
            self._connections[id(original)] = original
        return _Connection(self, original)

    def _retain_connection_failure(self, original: Any, error: BaseException) -> None:
        with self._owner_lock:
            self._connections[id(original)] = original
            self._connection_failures.setdefault(id(original), []).append(error)

    def _finish_original(self, original: Any) -> None:
        try:
            self._finish_pgconn(original.pgconn)
        except BaseException as error:
            self._retain_connection_failure(original, error)
            raise
    def _finish_pgconn(self, pgconn: Any) -> None:
        pgconn.finish()
        if pgconn.status != psycopg.pq.ConnStatus.BAD:
            raise ProtocolError('authority_ending_unresolved', 'Original PostgreSQL finish is unconfirmed')
        with self._owner_lock:
            # Retain only unresolved originals. Each failure entry's object is
            # held strongly by _connections until this same physical discharge.
            for identity, candidate in tuple(self._connections.items()):
                if candidate.pgconn is pgconn:
                    del self._connections[identity]
                    self._connection_failures.pop(identity, None)
            for record in tuple(self._connect_cleanup):
                if record.pgconn is pgconn:
                    for owner in record.owners:
                        owner.discard(record)

    def lifecycle_state(self) -> dict[str, Any]:
        with self._owner_lock:
            originals = {id(getattr(con, 'pgconn', con)) for con in self._connections.values()}
            originals.update(id(record.pgconn) for record in tuple(self._connect_cleanup))
            return {'state':self._owner_state, 'active_operations':self._active_operations, 'unresolved_connections':len(originals)}

    def close(self) -> None:
        # Serialize requested closes, but never hold an agent-lifetime control.
        with self._owner_lock:
            if self._owner_state == 'closed':
                return
            self._owner_state = 'closing'
            state = self.lifecycle_state()
            if state['active_operations']:
                raise ProtocolError('authority_ending_unresolved', 'Authority ending remains unresolved', details=state)
            seen: set[int] = set()
            for original in tuple(self._connections.values()):
                identity = id(original.pgconn)
                if identity in seen:
                    continue
                seen.add(identity)
                try:
                    self._finish_original(original)
                except Exception:
                    # The same original and failure are retained. No auto-loop.
                    continue
            for record in tuple(self._connect_cleanup):
                identity = id(record.pgconn)
                if identity in seen:
                    continue
                seen.add(identity)
                try:
                    self._finish_pgconn(record.pgconn)
                except BaseException as error:
                    record.cleanup_error = error
                    if not isinstance(error, Exception):
                        raise
            state = self.lifecycle_state()
            if state['active_operations'] or state['unresolved_connections']:
                raise ProtocolError('authority_ending_unresolved', 'Authority ending remains unresolved', details=state)
            self._owner_state = 'closed'


def operation_method(method: Any) -> Any:
    @wraps(method)
    def admitted(self: Any, *args: Any, **kwargs: Any) -> Any:
        owner = self.kernel if hasattr(self, 'kernel') else self
        with owner.operation_scope():
            return method(self, *args, **kwargs)
    return admitted
