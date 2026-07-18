// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {Script, console2} from "forge-std/Script.sol";
import {IPoolManager} from "v4-core/src/interfaces/IPoolManager.sol";
import {PhantomCreditToken} from "../src/hooks/PhantomCreditToken.sol";
import {MoebiusMEVHook} from "../src/hooks/MoebiusMEVHook.sol";

/// @title DeployMoebius
/// @notice One-command activation of the Moebius central-bank stack.
///
///         Resolves two hard constraints at once:
///         1. Circular dependency: pMEV immutably stores the hook address,
///            the hook immutably stores the pMEV address.
///         2. PoolManager requires a flag-less hook (zero callbacks) to sit at
///            an address with ZERO set bits in the lower 14 hook-flag bits.
///
///         Solution: predict pMEV's CREATE address from the deployer nonce,
///         then mine a CREATE2 salt for the hook (~16k iterations, seconds).
///
///         Env required:
///           DEPLOYER_PRIVATE_KEY  uint
///           POOL_MANAGER          address (Base: 0x498581fF718922c3f8e6A244956aF099B2652b2b)
///           TREASURY              address
///           MIN_BID_BPS           uint    (policy rate floor, e.g. 50 = 0.5%)
///
///         Run:
///           forge script script/DeployMoebius.s.sol --rpc-url $BASE_RPC \
///             --broadcast --verify --etherscan-api-key $BASESCAN_KEY
contract DeployMoebius is Script {
    uint160 internal constant ALL_HOOK_MASK = 0x3FFF;

    function run() external returns (PhantomCreditToken pToken, MoebiusMEVHook hook, bytes32 salt) {
        uint256 deployerKey = vm.envUint("DEPLOYER_PRIVATE_KEY");
        address deployer = vm.addr(deployerKey);
        address poolManager = vm.envAddress("POOL_MANAGER");
        address treasury = vm.envAddress("TREASURY");
        uint256 minBidBps = vm.envUint("MIN_BID_BPS");

        // 1. pMEV will deploy via CREATE at the deployer's next nonce
        address predictedPToken = vm.computeCreateAddress(deployer, vm.getNonce(deployer));

        // 2. Mine a salt giving the hook a zero-flag address
        bytes32 initHash = keccak256(
            abi.encodePacked(
                type(MoebiusMEVHook).creationCode, abi.encode(poolManager, predictedPToken, treasury, minBidBps)
            )
        );
        address predictedHook;
        for (uint256 i = 0;; i++) {
            salt = bytes32(i);
            predictedHook = vm.computeCreate2Address(salt, initHash, deployer);
            if (uint160(predictedHook) & ALL_HOOK_MASK == 0) break;
        }
        console2.log("Mined salt:", vm.toString(salt));

        // 3. Deploy
        vm.startBroadcast(deployerKey);
        pToken = new PhantomCreditToken(predictedHook);
        require(address(pToken) == predictedPToken, "nonce drifted - aborting");
        hook = new MoebiusMEVHook{salt: salt}(IPoolManager(poolManager), address(pToken), treasury, minBidBps);
        require(address(hook) == predictedHook, "CREATE2 drifted - aborting");
        vm.stopBroadcast();

        console2.log("PhantomCreditToken (pMEV):", address(pToken));
        console2.log("MoebiusMEVHook           :", address(hook));
        console2.log("Policy rate (minBidBps)  :", minBidBps);
        console2.log("NEXT STEPS:");
        console2.log("  1. initialize pMEV/<realAsset> pool with fee=0x800000 (dynamic), hooks=hook");
        console2.log("  2. owner: setPoolSwapFee(key, fee) then seedLiquidity(key, ...)");
        console2.log("  3. point searcher bots at executeMEV()");
    }
}
