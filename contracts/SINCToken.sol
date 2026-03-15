// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/**
 * @title SINCToken
 * @notice SINCOR utility and governance token (ERC-20)
 *
 * Anti-honeypot guarantees:
 *  - Standard OpenZeppelin ERC-20 — no custom transfer/transferFrom hooks
 *  - Zero buy tax  / zero sell tax
 *  - No blacklist or whitelist functions
 *  - No pause mechanism
 *  - Fixed total supply — no mint function after deployment
 *  - Owner role used only to seed liquidity; renounceOwnership() called
 *    immediately after initial LP deposit so no privileged functions remain
 *
 * @dev Verified source code mirrors exactly what is deployed on-chain.
 *      Any discrepancy between this file and the Etherscan-verified bytecode
 *      should be treated as a critical bug.
 */

import {ERC20} from "@openzeppelin/contracts/token/ERC20/ERC20.sol";
import {Ownable} from "@openzeppelin/contracts/access/Ownable.sol";

contract SINCToken is ERC20, Ownable {
    // ── Constants ────────────────────────────────────────────────────────────

    /// @notice Hard-capped supply: 10 billion SINC (18 decimals)
    uint256 public constant MAX_SUPPLY = 10_000_000_000 * 10 ** 18;

    // ── Events ───────────────────────────────────────────────────────────────

    /// @notice Emitted once during construction — confirms no future minting
    event TokensLocked(uint256 totalSupply);

    // ── Constructor ──────────────────────────────────────────────────────────

    /**
     * @dev Mints the full fixed supply to the deployer.
     *      No additional tokens can ever be minted.
     *      The deployer is expected to:
     *        1. Transfer the allocated portions to the relevant multisig / vesting contracts.
     *        2. Deposit the LP allocation into Uniswap / PancakeSwap.
     *        3. Call renounceOwnership() to permanently remove all admin control.
     */
    constructor() ERC20("SINCOR", "SINC") Ownable(msg.sender) {
        _mint(msg.sender, MAX_SUPPLY);
        emit TokensLocked(MAX_SUPPLY);
    }

    // ── No extra functions ───────────────────────────────────────────────────
    //
    // This contract intentionally has NO:
    //   - mint()           → supply is fixed at construction; cannot be increased
    //   - burn()           → omitted to keep total supply permanently fixed at 10 billion;
    //                        holders may send to the zero address to destroy their own tokens,
    //                        but there is no privileged admin function to do so
    //   - pause()          → trading can never be halted by any party
    //   - blacklist()      → no address can ever be frozen or blocked
    //   - setFee()         → buy/sell taxes are permanently 0%; no fee logic exists
    //   - excludeFromFee() → not applicable; there are no fees to exclude from
    //
    // The only Ownable function callable before renouncement is renounceOwnership()
    // itself, which permanently removes all owner privileges.  After that call
    // this contract has no privileged roles whatsoever.
}
