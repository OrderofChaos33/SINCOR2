"""EIP-712 typed Bid payloads with secp256k1 or HMAC-SHA256 signatures.

The digest is the canonical Ethereum typed-data hash:

    keccak256("\\x19\\x01" || domainSeparator || structHash)

``eth_account`` signs that digest when a secp256k1 key is present. The HMAC
path is a portable fallback used by the demo roster and environments that
cannot load ``eth_account``. Both schemes bind the same digest, so a bid
cannot be replayed across auctions or domains.
"""

from __future__ import annotations

import hashlib
import hmac
from typing import Any, Dict, Optional, Tuple

from .keccak import keccak256
from .types import ContractNetConfig, SigType

EIP712_DOMAIN_TYPE = (
    "EIP712Domain(string name,string version,uint256 chainId,address verifyingContract)"
)
BID_TYPE = (
    "Bid(bytes32 auctionId,string taskId,address agent,"
    "uint256 price,uint256 estimatedTokens,uint256 nonce,uint256 deadline,"
    "string epochId,bytes32 epochRoot)"
)
EMPTY_EPOCH_ROOT = "0x" + "00" * 32

EIP712_DOMAIN_TYPEHASH = keccak256(EIP712_DOMAIN_TYPE.encode("ascii"))
BID_TYPEHASH = keccak256(BID_TYPE.encode("ascii"))


def _strip0x(value: str) -> str:
    value = value.strip()
    if value.startswith(("0x", "0X")):
        return value[2:]
    return value


def to_hex(data: bytes) -> str:
    return "0x" + data.hex()


def encode_uint256(value: int) -> bytes:
    if value < 0 or value >= 1 << 256:
        raise ValueError("uint256 out of range")
    return int(value).to_bytes(32, "big")


def encode_address(addr: str) -> bytes:
    raw = _strip0x(addr)
    if len(raw) != 40:
        raise ValueError(f"address must be 20 bytes, got {addr!r}")
    try:
        body = bytes.fromhex(raw)
    except ValueError as exc:
        raise ValueError(f"invalid address {addr!r}") from exc
    return body.rjust(32, b"\x00")


def encode_bytes32(value: str | bytes) -> bytes:
    if isinstance(value, bytes):
        raw = value
    else:
        raw = bytes.fromhex(_strip0x(value))
    if len(raw) != 32:
        raise ValueError("bytes32 must be 32 bytes")
    return raw


def checksum_address(addr: str) -> str:
    """EIP-55 checksum."""
    hex_addr = _strip0x(addr).lower()
    if len(hex_addr) != 40:
        raise ValueError("address must be 20 bytes")
    hashed = keccak256(hex_addr.encode("ascii")).hex()
    out = ["0x"]
    for i, ch in enumerate(hex_addr):
        if ch.isalpha() and int(hashed[i], 16) >= 8:
            out.append(ch.upper())
        else:
            out.append(ch)
    return "".join(out)



def demo_wallet(agent_id: str) -> str:
    digest = keccak256(b"sincor-cn/" + agent_id.encode("utf-8"))
    return checksum_address("0x" + digest[:20].hex())


def demo_signing_secret(agent_id: str) -> str:
    return keccak256(b"sincor-cn-key/" + agent_id.encode("utf-8")).hex()


def domain_separator(config: ContractNetConfig) -> bytes:
    return keccak256(
        EIP712_DOMAIN_TYPEHASH
        + keccak256(config.domain_name.encode("utf-8"))
        + keccak256(config.domain_version.encode("utf-8"))
        + encode_uint256(config.chain_id)
        + encode_address(config.verifying_contract)
    )


def struct_hash(
    *,
    auction_id: str,
    task_id: str,
    agent: str,
    price: int,
    estimated_tokens: int,
    nonce: int,
    deadline: int,
    epoch_id: str = "",
    epoch_root: str = EMPTY_EPOCH_ROOT,
) -> bytes:
    return keccak256(
        BID_TYPEHASH
        + encode_bytes32(auction_id)
        + keccak256(task_id.encode("utf-8"))
        + encode_address(agent)
        + encode_uint256(price)
        + encode_uint256(estimated_tokens)
        + encode_uint256(nonce)
        + encode_uint256(deadline)
        + keccak256(epoch_id.encode("utf-8"))
        + encode_bytes32(epoch_root)
    )


def typed_data_digest(
    config: ContractNetConfig,
    *,
    auction_id: str,
    task_id: str,
    agent: str,
    price: int,
    estimated_tokens: int,
    nonce: int,
    deadline: int,
    epoch_id: str = "",
    epoch_root: str = EMPTY_EPOCH_ROOT,
) -> bytes:
    return keccak256(
        b"\x19\x01"
        + domain_separator(config)
        + struct_hash(
            auction_id=auction_id,
            task_id=task_id,
            agent=agent,
            price=price,
            estimated_tokens=estimated_tokens,
            nonce=nonce,
            deadline=deadline,
            epoch_id=epoch_id,
            epoch_root=epoch_root,
        )
    )


def typed_data_payload(
    config: ContractNetConfig,
    *,
    auction_id: str,
    task_id: str,
    agent: str,
    price: int,
    estimated_tokens: int,
    nonce: int,
    deadline: int,
    epoch_id: str = "",
    epoch_root: str = EMPTY_EPOCH_ROOT,
) -> Dict[str, Any]:
    return {
        "types": {
            "EIP712Domain": [
                {"name": "name", "type": "string"},
                {"name": "version", "type": "string"},
                {"name": "chainId", "type": "uint256"},
                {"name": "verifyingContract", "type": "address"},
            ],
            "Bid": [
                {"name": "auctionId", "type": "bytes32"},
                {"name": "taskId", "type": "string"},
                {"name": "agent", "type": "address"},
                {"name": "price", "type": "uint256"},
                {"name": "estimatedTokens", "type": "uint256"},
                {"name": "nonce", "type": "uint256"},
                {"name": "deadline", "type": "uint256"},
                {"name": "epochId", "type": "string"},
                {"name": "epochRoot", "type": "bytes32"},
            ],
        },
        "primaryType": "Bid",
        "domain": {
            "name": config.domain_name,
            "version": config.domain_version,
            "chainId": config.chain_id,
            "verifyingContract": config.verifying_contract,
        },
        "message": {
            "auctionId": auction_id if auction_id.startswith("0x") else "0x" + auction_id,
            "taskId": task_id,
            "agent": agent,
            "price": str(price),
            "estimatedTokens": str(estimated_tokens),
            "nonce": str(nonce),
            "deadline": str(deadline),
            "epochId": epoch_id,
            "epochRoot": epoch_root,
        },
    }


def _secret_bytes(secret: str) -> bytes:
    raw = _strip0x(secret)
    try:
        if len(raw) % 2 == 0 and raw:
            return bytes.fromhex(raw)
    except ValueError:
        pass
    return keccak256(secret.encode("utf-8"))


def sign_hmac(digest: bytes, secret: str) -> str:
    mac = hmac.new(_secret_bytes(secret), digest, hashlib.sha256).digest()
    return to_hex(mac)


def verify_hmac(digest: bytes, signature: str, secret: str) -> bool:
    expected = bytes.fromhex(_strip0x(sign_hmac(digest, secret)))
    got = bytes.fromhex(_strip0x(signature))
    if len(expected) != len(got):
        return False
    return hmac.compare_digest(expected, got)


def _sign_secp256k1(digest: bytes, private_key: str) -> Optional[str]:
    try:
        from eth_account import Account
    except Exception:
        return None
    key = private_key if private_key.startswith("0x") else "0x" + private_key
    try:
        signed = Account.from_key(key).unsafe_sign_hash(digest)
    except Exception:
        return None
    sig = signed.signature.hex()
    return sig if sig.startswith("0x") else "0x" + sig


def _recover_secp256k1(digest: bytes, signature: str) -> Optional[str]:
    try:
        from eth_account import Account
    except Exception:
        return None
    try:
        recovered = Account._recover_hash(digest, signature=signature)  # noqa: SLF001
    except Exception:
        try:
            recovered = Account.recover_message  # type: ignore[attr-defined]
            recovered = Account._recover_hash(digest, signature=signature)  # noqa: SLF001
        except Exception:
            return None
    return recovered


def sign_digest(
    digest: bytes,
    *,
    private_key: str = "",
    signing_secret: str = "",
) -> Tuple[str, str]:
    """Return (signature, sig_type). Prefers secp256k1 when a key is usable."""
    if private_key:
        sig = _sign_secp256k1(digest, private_key)
        if sig:
            return sig, SigType.SECP256K1.value
    if not signing_secret:
        raise ValueError("signing_secret required when secp256k1 is unavailable")
    return sign_hmac(digest, signing_secret), SigType.HMAC.value


def verify_digest(
    digest: bytes,
    signature: str,
    *,
    sig_type: str,
    expected_address: str = "",
    signing_secret: str = "",
) -> bool:
    if sig_type == SigType.SECP256K1.value:
        recovered = _recover_secp256k1(digest, signature)
        if recovered is None:
            return False
        return recovered.lower() == expected_address.lower()
    if sig_type == SigType.HMAC.value:
        if not signing_secret:
            return False
        return verify_hmac(digest, signature, signing_secret)
    return False
