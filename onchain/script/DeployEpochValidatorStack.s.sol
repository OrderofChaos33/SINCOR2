// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {Script, console} from "forge-std/Script.sol";
import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";

import {EpochSessionKeyValidator} from "../src/sincor/EpochSessionKeyValidator.sol";
import {AgentStakeSlash} from "../src/sincor/AgentStakeSlash.sol";
import {MemoryBoundSettlement} from "../src/sincor/MemoryBoundSettlement.sol";

/// @notice Deploy ERC-7579 epoch validator + staking/slashing + settlement gate on Base.
contract DeployEpochValidatorStack is Script {
    address constant TREASURY = 0x09E2891432827D8835d2E9b83B25e2a5ba9612Ac;

    function run() external {
        uint256 pk = vm.envUint("PRIVATE_KEY");
        uint256 expectedChain = vm.envOr("TARGET_CHAIN_ID", uint256(8453));
        address admin = vm.envOr("EPOCH_ADMIN", vm.addr(pk));
        address collateral = vm.envAddress("COLLATERAL_TOKEN");
        address payout = vm.envAddress("PAYOUT_TOKEN");
        uint256 minStake = vm.envOr("MIN_AGENT_STAKE", uint256(1e18));

        require(block.chainid == expectedChain, "wrong-chain");

        vm.startBroadcast(pk);
        EpochSessionKeyValidator validator = new EpochSessionKeyValidator(admin);
        AgentStakeSlash stakeSlash = new AgentStakeSlash(admin, IERC20(collateral), TREASURY, minStake);
        MemoryBoundSettlement settlement = new MemoryBoundSettlement(admin, validator, stakeSlash, IERC20(payout));
        stakeSlash.grantRole(stakeSlash.SLASHER_ROLE(), address(settlement));
        vm.stopBroadcast();

        console.log("EpochSessionKeyValidator:", address(validator));
        console.log("AgentStakeSlash:", address(stakeSlash));
        console.log("MemoryBoundSettlement:", address(settlement));
    }
}
