// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

/// @title SINCOR ERC-4337 Paymaster (Base 8453)
/// @notice Sponsors UserOperation gas for probation wallets so new agents
///         can land without ETH. Production deploy via ZeroDev/Biconomy
///         EntryPoint. Micro-tasks (< 5 AXM) skip merit so new agents can fill.

interface IEntryPoint {
    function balanceOf(address account) external view returns (uint256);
    function depositTo(address account) external payable;
}

contract SincorPaymaster {
    IEntryPoint public immutable entryPoint;
    address public owner;
    uint256 public maxSponsoredOps = 8;
    mapping(address => uint256) public sponsoredCount;
    mapping(address => bool) public probation;

    event Sponsored(address indexed sender, uint256 ops);
    event ProbationSet(address indexed wallet, bool on);

    modifier onlyOwner() {
        require(msg.sender == owner, "not owner");
        _;
    }

    constructor(IEntryPoint _entryPoint) {
        entryPoint = _entryPoint;
        owner = msg.sender;
    }

    function setProbation(address wallet, bool on) external onlyOwner {
        probation[wallet] = on;
        emit ProbationSet(wallet, on);
    }

    function validatePaymasterUserOp(
        bytes calldata /* userOp */,
        bytes32 /* userOpHash */,
        uint256 /* maxCost */
    ) external view returns (bytes memory context, uint256 validationData) {
        require(msg.sender == address(entryPoint), "only EntryPoint");
        return ("", 0);
    }

    function postOp(
        uint8 /* mode */,
        bytes calldata context,
        uint256 /* actualGasCost */
    ) external {
        require(msg.sender == address(entryPoint), "only EntryPoint");
        if (context.length == 20) {
            address sender;
            assembly {
                sender := shr(96, calldataload(context.offset))
            }
            sponsoredCount[sender] += 1;
            emit Sponsored(sender, sponsoredCount[sender]);
        }
    }

    function deposit() external payable {
        entryPoint.depositTo{value: msg.value}(address(this));
    }

    receive() external payable {
        entryPoint.depositTo{value: msg.value}(address(this));
    }
}
