// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

// LendingLoopHookFactory.sol
// Clonable factory for all SINCOR Lending Loop Hook variants.
// Uses minimal proxy pattern for gas-efficient deployment.
// Production: CREATE2 ready (extend your mine script). One factory per hook type or universal.

 import {BaseLendingLoopHookContainer} from "./BaseLendingLoopHookContainer.sol";
import {IPoolManager} from "@uniswap/v4-core/src/interfaces/IPoolManager.sol";
import {Clones} from "@openzeppelin/contracts/proxy/Clones.sol";

contract LendingLoopHookFactory {
    address public immutable implementation; // BaseLendingLoopHookContainer impl
    address public immutable guardian;

    event HookDeployed(address indexed hook, address indexed deployer, bytes32 salt);

    constructor(address _implementation, address _guardian) {
        implementation = _implementation;
        guardian = _guardian;
    }

    /// @notice Deploy a new clonable hook variant
    function deployHook(
        bytes32 salt,
        address lendingProtocol,
        uint256 maxLTV,
        uint256 loopMultiplier,
        address[] calldata initialAgents
    ) external returns (address hook) {
        hook = Clones.cloneDeterministic(implementation, salt);
        BaseLendingLoopHookContainer(hook).initialize(lendingProtocol, maxLTV, loopMultiplier, initialAgents);
        emit HookDeployed(hook, msg.sender, salt);
    }

    function predictAddress(bytes32 salt) external view returns (address) {
        return Clones.predictDeterministicAddress(implementation, salt, address(this));
    }
}
