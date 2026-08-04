// SPDX-License-Identifier: MIT
pragma solidity 0.8.26;

import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {SafeERC20} from "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";
import {MerkleProof} from "@openzeppelin/contracts/utils/cryptography/MerkleProof.sol";
import {Ownable} from "@openzeppelin/contracts/access/Ownable.sol";
import {Pausable} from "@openzeppelin/contracts/utils/Pausable.sol";
import {ReentrancyGuard} from "@openzeppelin/contracts/utils/ReentrancyGuard.sol";

/**
 * @title  HarvestClaim
 * @notice Merkle-tree claim contract for the SINCOR Harvest Moon activation.
 *
 *         Eligible wallets receive a fixed allocation of SINC utility access
 *         credits from a pre-funded treasury slice — no new mint occurs.
 *
 *         Key properties:
 *         - One claim per wallet (bitmap enforced on-chain)
 *         - Merkle root set once; owner renounces / transfers to multi-sig after
 *         - 30-day claim window (configurable at deploy)
 *         - Emergency pause callable by owner in < 2 minutes
 *         - Zero admin-mint capability
 *
 * Legal: SINC utility access credits only. No investment value implied.
 */
contract HarvestClaim is Ownable, Pausable, ReentrancyGuard {
    using SafeERC20 for IERC20;

    // ─────────────────────────────────────────────────────────────────────────
    // Types & storage
    // ─────────────────────────────────────────────────────────────────────────

    IERC20 public immutable sinc;
    address public immutable treasury;

    bytes32 public merkleRoot;
    bool    public rootFinalized;          // root can only be set once
    uint256 public claimWindowEnd;         // unix timestamp
    uint256 public totalAllocated;         // tokens loaded into contract
    uint256 public totalClaimed;

    mapping(address => bool) public claimed;

    // ─────────────────────────────────────────────────────────────────────────
    // Events
    // ─────────────────────────────────────────────────────────────────────────

    event RootSet(bytes32 indexed root, uint256 windowEnd, uint256 allocation);
    event Claimed(address indexed account, uint256 amount);
    event WindowExtended(uint256 newEnd);
    event Swept(address indexed to, uint256 amount);

    // ─────────────────────────────────────────────────────────────────────────
    // Errors
    // ─────────────────────────────────────────────────────────────────────────

    error RootAlreadyFinalized();
    error RootNotSet();
    error WindowClosed();
    error AlreadyClaimed();
    error InvalidProof();
    error ZeroAmount();
    error ZeroAddress();
    error WindowNotClosed();
    error NothingToSweep();

    // ─────────────────────────────────────────────────────────────────────────
    // Constructor
    // ─────────────────────────────────────────────────────────────────────────

    /**
     * @param _sinc     Address of the SINC ERC-20 token (Base mainnet)
     * @param _treasury Treasury address — receives unclaimed tokens after window
     * @param _owner    Initial owner (deployer); should transfer to multi-sig
     */
    constructor(address _sinc, address _treasury, address _owner) Ownable(_owner) {
        if (_sinc == address(0) || _treasury == address(0)) revert ZeroAddress();
        sinc     = IERC20(_sinc);
        treasury = _treasury;
    }

    // ─────────────────────────────────────────────────────────────────────────
    // Owner-only administration
    // ─────────────────────────────────────────────────────────────────────────

    /**
     * @notice Set the Merkle root exactly once, opening the claim window.
     *         Caller must have already transferred `_allocation` SINC to this
     *         contract from the treasury.
     *
     * @param _root       Merkle root of (address, amount) leaves
     * @param _windowDays How many days the claim window stays open (e.g. 30)
     * @param _allocation Total SINC tokens deposited for this campaign
     */
    function setRoot(
        bytes32 _root,
        uint256 _windowDays,
        uint256 _allocation
    ) external onlyOwner {
        if (rootFinalized)          revert RootAlreadyFinalized();
        if (_root == bytes32(0))    revert RootNotSet();
        if (_windowDays == 0)       revert ZeroAmount();
        if (_allocation == 0)       revert ZeroAmount();

        // Verify the contract actually holds enough tokens
        require(
            sinc.balanceOf(address(this)) >= _allocation,
            "HarvestClaim: insufficient balance"
        );

        merkleRoot      = _root;
        claimWindowEnd  = block.timestamp + (_windowDays * 1 days);
        totalAllocated  = _allocation;
        rootFinalized   = true;

        emit RootSet(_root, claimWindowEnd, _allocation);
    }

    /**
     * @notice Extend the claim window. Can only push further into the future.
     */
    function extendWindow(uint256 newEnd) external onlyOwner {
        require(newEnd > claimWindowEnd, "HarvestClaim: new end must be later");
        claimWindowEnd = newEnd;
        emit WindowExtended(newEnd);
    }

    /**
     * @notice Pause all claims (emergency use only).
     */
    function pause() external onlyOwner {
        _pause();
    }

    /**
     * @notice Unpause claims.
     */
    function unpause() external onlyOwner {
        _unpause();
    }

    /**
     * @notice Sweep unclaimed tokens back to treasury after window closes.
     *         Permanently closes claims for any remaining unclaimed allocations.
     */
    function sweep() external onlyOwner {
        if (claimWindowEnd == 0 || block.timestamp <= claimWindowEnd) {
            revert WindowNotClosed();
        }
        uint256 balance = sinc.balanceOf(address(this));
        if (balance == 0) revert NothingToSweep();
        sinc.safeTransfer(treasury, balance);
        emit Swept(treasury, balance);
    }

    // ─────────────────────────────────────────────────────────────────────────
    // Public claim
    // ─────────────────────────────────────────────────────────────────────────

    /**
     * @notice Claim SINC utility access credits.
     *
     * @param proof  Merkle proof for (msg.sender, amount)
     * @param amount Token amount encoded in the leaf (must match tree)
     */
    function claim(bytes32[] calldata proof, uint256 amount)
        external
        nonReentrant
        whenNotPaused
    {
        if (!rootFinalized)                          revert RootNotSet();
        if (block.timestamp > claimWindowEnd)        revert WindowClosed();
        if (claimed[msg.sender])                     revert AlreadyClaimed();
        if (amount == 0)                             revert ZeroAmount();

        bytes32 leaf = keccak256(abi.encodePacked(msg.sender, amount));
        if (!MerkleProof.verifyCalldata(proof, merkleRoot, leaf)) {
            revert InvalidProof();
        }

        claimed[msg.sender] = true;
        totalClaimed += amount;

        sinc.safeTransfer(msg.sender, amount);

        emit Claimed(msg.sender, amount);
    }

    // ─────────────────────────────────────────────────────────────────────────
    // View helpers
    // ─────────────────────────────────────────────────────────────────────────

    /**
     * @notice Returns true if a proof is valid and the address has not yet claimed.
     */
    function isEligible(
        address account,
        uint256 amount,
        bytes32[] calldata proof
    ) external view returns (bool) {
        if (claimed[account]) return false;
        bytes32 leaf = keccak256(abi.encodePacked(account, amount));
        return MerkleProof.verifyCalldata(proof, merkleRoot, leaf);
    }

    /**
     * @notice Remaining unclaimed allocation (may include tokens not yet transferred).
     */
    function remainingAllocation() external view returns (uint256) {
        return sinc.balanceOf(address(this));
    }

    /**
     * @notice True if the claim window is currently open.
     */
    function claimWindowOpen() external view returns (bool) {
        return rootFinalized
            && !paused()
            && block.timestamp <= claimWindowEnd;
    }
}
