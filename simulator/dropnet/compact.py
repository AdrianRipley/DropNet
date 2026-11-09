# SPDX-License-Identifier: GPL-3.0-or-later
"""Bounded binary inventory summaries shared by simulation and local transport.

Fingerprints are not message identities or authentication. Truncation to 64 bits
admits collisions; changing the salt avoids persistent accidental suppression.
No Bloom filter, shared delivery ledger, or receiver-state oracle is involved.
"""
from dataclasses import dataclass
import hashlib
import json
import math
import struct


HEADER = struct.Struct('>4sBBHQ')
MAGIC = b'DNCS'
VERSION = 1
KIND = 1
TOKEN_BYTES = 8
MAX_TOKENS = 65535
MAX_FRAME_BYTES = HEADER.size + MAX_TOKENS * TOKEN_BYTES
MAX_MODELED_DATA_BYTES = 608  # 512 UTF-8 payload + legacy 96-byte metadata.


def _salt_bytes(salt):
    if type(salt) is not int or not 0 <= salt < 2**64:
        raise ValueError('salt must be an unsigned 64-bit integer')
    return salt.to_bytes(8, 'big')


def fingerprint(message_id, salt):
    """An eight-byte comparison token; it is neither encryption nor a signature."""
    salt_bytes = _salt_bytes(salt)
    if not isinstance(message_id, str):
        raise ValueError('ID must be text')
    try:
        encoded = message_id.encode('utf-8')
    except UnicodeError as exc:
        raise ValueError('ID must be valid UTF-8') from exc
    if not 1 <= len(encoded) <= 128:
        raise ValueError('ID must contain 1..128 UTF-8 bytes')
    return hashlib.sha256(salt_bytes + encoded).digest()[:TOKEN_BYTES]


@dataclass(frozen=True)
class Summary:
    salt: int
    tokens: frozenset

    def contains(self, message_id):
        return fingerprint(message_id, self.salt) in self.tokens


def encode_summary(salt, tokens):
    _salt_bytes(salt)
    values = list(tokens)
    if any(not isinstance(token, bytes) or len(token) != TOKEN_BYTES for token in values):
        raise ValueError('tokens must each be eight bytes')
    values = sorted(set(values))
    if len(values) > MAX_TOKENS:
        raise ValueError('too many tokens')
    return HEADER.pack(MAGIC, VERSION, KIND, len(values), salt) + b''.join(values)


def decode_summary(frame):
    if not isinstance(frame, bytes) or not HEADER.size <= len(frame) <= MAX_FRAME_BYTES:
        raise ValueError('invalid summary frame size')
    magic, version, kind, count, salt = HEADER.unpack_from(frame)
    if (magic, version, kind) != (MAGIC, VERSION, KIND):
        raise ValueError('unsupported summary header')
    if len(frame) != HEADER.size + TOKEN_BYTES * count:
        raise ValueError('summary count/length mismatch')
    tokens = [frame[i:i+TOKEN_BYTES] for i in range(HEADER.size, len(frame), TOKEN_BYTES)]
    if tokens != sorted(set(tokens)):
        raise ValueError('summary tokens must be strictly increasing')
    return Summary(salt, frozenset(tokens))


def plan_frames(ids, salt, quota):
    """Return a fixed schedule, decided before any success or loss is observed.

    Complete small inventories repeat twice when both copies fit. Otherwise send
    one summary containing as many lowest salted tokens as fit. Empty inventories
    send nothing: an absent summary already means suppress no candidate.
    """
    _salt_bytes(salt)
    if type(quota) is not int or quota < 0:
        raise ValueError('quota must be a nonnegative integer')
    tokens = sorted({fingerprint(mid, salt) for mid in ids})
    fit = min(MAX_TOKENS, max(0, (quota - HEADER.size) // TOKEN_BYTES))
    if not tokens or not fit:
        return []
    packet = encode_summary(salt, tokens[:fit])
    if len(tokens) <= fit and 2 * len(packet) <= quota:
        return [packet, packet]
    return [packet]


def control_budget(budget, duration, rate):
    """At most a quarter of usable contact capacity; reserve max-size DATA."""
    if (type(budget) is not int or budget < 0
            or any(isinstance(v, bool) or not isinstance(v, (int, float))
                   or not math.isfinite(v) for v in (duration, rate))
            or duration < 0 or rate <= 0):
        raise ValueError('invalid contact capacity')
    time_bytes = duration * rate
    effective = budget if time_bytes >= budget else math.floor(time_bytes)
    return min(effective // 4, max(0, effective - MAX_MODELED_DATA_BYTES))


def contact_salt(ordinal, start, a, b):
    """Deterministic, public contact nonce, independent of all loss RNG draws."""
    if type(ordinal) is not int or ordinal < 0:
        raise ValueError('contact ordinal must be nonnegative')
    value = json.dumps([ordinal, start, a, b], ensure_ascii=False,
                       separators=(',', ':'), allow_nan=False).encode('utf-8')
    return int.from_bytes(hashlib.sha256(value).digest()[:8], 'big')
