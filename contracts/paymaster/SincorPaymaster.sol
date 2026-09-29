// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

/// @title SINCOR ERC-4337 Paymaster (Base 8453)
/// @notice Sponsors UserOperation gas for probation wallets so new agents
///         can land without ETH. Production deploy via ZeroDev/Biconomy
///         EntryPoint. Micro-tasks (< 5 AXM) skip merit so new agents can fill.
///
/// @dev P0/W-17 hardening: validatePaymasterUserOp enforces, in order:
///      1. the caller is the EntryPoint,
///      2. the UserOp sender is explicitly allowlisted: probation[sender]
///         must be true (set via setProbation by the owner; default false
///         means NOT sponsored — the mapping is an allowlist, not a
///         denylist),
///      3. the sender is under its per-wallet sponsored-op cap, counting
///         in-flight ops: sponsoredCount[sender] + inFlight[sender] <
///         maxSponsoredOps,
///      4. the global spend ceiling holds: sponsoredWei (settled) +
///         reservedWei (in-flight) + maxCost <= maxSponsoredWei.
///      The maxCost of each accepted op is reserved until postOp settles the
///      actual gas cost, so the ceiling holds even with concurrent in-flight
///      ops. maxSponsoredWei defaults to 0 (fail-closed: nothing is sponsored
///      until the owner opens a ceiling).
///      Bundle-cap fix (adversarial review): every accepted validate marks
///      the sender's op in-flight (inFlight[sender] += 1) and postOp clears
///      it. Without this, N UserOps from one sender in a single EntryPoint
///      handleOps bundle would all validate (sponsoredCount unchanged
///      between validates) and settle together, blowing past the per-wallet
///      cap. Validate always returns non-empty context (abi.encode(sender,
///      maxCost), 64 bytes), so a conformant EntryPoint always calls postOp
///      and the in-flight slot is always released; the postOp decrement is
///      additionally guarded against underflow.
///      Single canonical encode path (E2): context = abi.encode(sender,
///      maxCost); postOp decodes it with abi.decode. The old raw-20-byte
///      context path is removed.

interface IEntryPoint {
    function balanceOf(address account) external view returns (uint256);
    function depositTo(address account) external payable;
}

contract SincorPaymaster {
    IEntryPoint public immutable entryPoint;
    address public owner;
    uint256 public maxSponsoredOps = 8;
    /// @notice Global spend ceiling in wei. 0 = sponsorship closed.
    uint256 public maxSponsoredWei;
    /// @notice Gas wei already settled via postOp.
    uint256 public sponsoredWei;
    /// @notice Sum of maxCost reserved by in-flight validated ops.
    uint256 public reservedWei;
    mapping(address => uint256) public sponsoredCount;
    /// @notice Per-wallet ops validated but not yet settled via postOp.
    mapping(address => uint256) public inFlight;
    /// @notice Allowlist: only wallets with probation == true get sponsored.
    mapping(address => bool) public probation;

    event Sponsored(address indexed sender, uint256 ops);
    event ProbationSet(address indexed wallet, bool on);
    event MaxSponsoredOpsSet(uint256 ops);
    event MaxSponsoredWeiSet(uint256 weiLimit);

    modifier onlyOwner() {
        require(msg.sender == owner, "not owner");
        _;
    }

    constructor(IEntryPoint _entryPoint) {
        entryPoint = _entryPoint;
        owner = msg.sender;
    }

    function setProbation(address wallet, bool on) external onlyOwner {
        require(wallet != address(0), "zero wallet");
        probation[wallet] = on;
        emit ProbationSet(wallet, on);
    }

    function setMaxSponsoredOps(uint256 ops) external onlyOwner {
        maxSponsoredOps = ops;
        emit MaxSponsoredOpsSet(ops);
    }

    function setMaxSponsoredWei(uint256 weiLimit) external onlyOwner {
        maxSponsoredWei = weiLimit;
        emit MaxSponsoredWeiSet(weiLimit);
    }

    /// @dev Decodes the sender from an unpacked ERC-4337 UserOperation
    ///      (sender is the first 32-byte word, address right-aligned).
    function _senderOf(bytes calldata userOp)
        internal
        pure
        returns (address sender)
    {
        require(userOp.length >= 32, "bad userOp");
        bytes32 word;
        assembly {
            word := calldataload(userOp.offset)
        }
        sender = address(uint160(uint256(word)));
    }

    function validatePaymasterUserOp(
        bytes calldata userOp,
        bytes32 /* userOpHash */,
        uint256 maxCost
    ) external returns (bytes memory context, uint256 validationData) {
        require(msg.sender == address(entryPoint), "only EntryPoint");
        address sender = _senderOf(userOp);
        require(probation[sender], "not allowlisted");
        require(
            sponsoredCount[sender] + inFlight[sender] < maxSponsoredOps,
            "op cap reached"
        );
        require(
            sponsoredWei + reservedWei + maxCost <= maxSponsoredWei,
            "spend ceiling"
        );
        reservedWei += maxCost;
        inFlight[sender] += 1;
        // Canonical context for postOp: (sender, maxCost).
        return (abi.encode(sender, maxCost), 0);
    }

    function postOp(
        uint8 /* mode */,
        bytes calldata context,
        uint256 actualGasCost
    ) external {
        require(msg.sender == address(entryPoint), "only EntryPoint");
        (address sender, uint256 maxCost) = abi.decode(context, (address, uint256));
        if (inFlight[sender] > 0) {
            // A conformant EntryPoint always pairs postOp with a matching
            // validate (validate always returns non-empty context), but an
            // unmatched postOp must never underflow the counter.
            inFlight[sender] -= 1;
        }
        if (reservedWei >= maxCost) {
            reservedWei -= maxCost;
        } else {
            reservedWei = 0;
        }
        sponsoredWei += actualGasCost;
        sponsoredCount[sender] += 1;
        emit Sponsored(sender, sponsoredCount[sender]);
    }

    function deposit() external payable {
        entryPoint.depositTo{value: msg.value}(address(this));
    }

    receive() external payable {
        entryPoint.depositTo{value: msg.value}(address(this));
    }
}
