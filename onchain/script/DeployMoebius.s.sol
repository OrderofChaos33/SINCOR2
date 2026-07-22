// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {Script, console2} from "forge-std/Script.sol";
import {IPoolManager} from "v4-core/src/interfaces/IPoolManager.sol";
import {Hooks} from "v4-core/src/libraries/Hooks.sol";
import {MoebiusMEVHook} from "../src/hooks/MoebiusMEVHook.sol";
import {PhantomCreditToken} from "../src/hooks/PhantomCreditToken.sol";

/// @title DeployMoebius — two-step CREATE2 deploy for the zero-flag Moebius hook
/// @notice The hook carries ZERO hook-permission flags: the lower 14 bits of its
///         address must all be zero. We mine a salt off-chain (or here, linearly)
///         for the hook deployed via CREATE2, then deploy PhantomCreditToken with
///         the mined hook address.
///
///         Usage:
///           forge script script/DeployMoebius.s.sol \
///             --rpc-url $BASE_RPC --broadcast --verify
///
///         Env:
///           PRIVATE_KEY        deployer key
///           POOL_MANAGER       v4 PoolManager on the target chain
///           PROTOCOL_TREASURY  treasury that receives 20% of escrowed bids
///           MIN_BID_BPS        initial policy rate floor (e.g. 50 = 0.5%)
contract DeployMoebius is Script {
    // CREATE2 deployer used by Foundry broadcasts
    address internal constant CREATE2_DEPLOYER = 0x4e59b44847b379578588920cA78FbF26c0B4956C;

    function run() external {
        address poolManager = vm.envAddress("POOL_MANAGER");
        address treasury = vm.envAddress("PROTOCOL_TREASURY");
        uint256 minBidBps = vm.envUint("MIN_BID_BPS");
        uint256 deployerKey = vm.envUint("PRIVATE_KEY");
        address deployer = vm.addr(deployerKey);

        // Mine a salt giving the hook address zero lower-14 flag bits.
        // Hook initcode embeds pToken=placeholder; we deploy the token AFTER the
        // hook, pointing the token at the mined hook address, so the hook's
        // pToken immutable must be mined against the token's CREATE2 address too.
        // Two fixed points -> we iterate: mine hook salt assuming token address T,
        // where T is computable from the hook's own address (token deployed by the
        // hook? No - token deployed by deployer with hook as ctor arg, via CREATE2
        // as well so both addresses are deterministic).
        (bytes32 hookSalt, address hookAddr, bytes32 tokenSalt, address tokenAddr) =
            _minePair(deployer, poolManager, treasury, minBidBps);

        console2.log("mined hook  :", hookAddr);
        console2.log("mined pMEV  :", tokenAddr);

        vm.startBroadcast(deployerKey);

        PhantomCreditToken token = new PhantomCreditToken{salt: tokenSalt}(hookAddr);
        require(address(token) == tokenAddr, "token address mismatch");

        MoebiusMEVHook hook =
            new MoebiusMEVHook{salt: hookSalt}(IPoolManager(poolManager), tokenAddr, treasury, minBidBps);
        require(address(hook) == hookAddr, "hook address mismatch");
        require(uint160(address(hook)) & Hooks.ALL_HOOK_MASK == 0, "hook flags not zero");

        vm.stopBroadcast();

        console2.log("MoebiusMEVHook deployed:", address(hook));
        console2.log("PhantomCreditToken deployed:", address(token));
    }

    function _minePair(address deployer, address poolManager, address treasury, uint256 minBidBps)
        internal
        pure
        returns (bytes32 hookSalt, address hookAddr, bytes32 tokenSalt, address tokenAddr)
    {
        // Outer loop: pick token salt, derive token address, then find a hook salt
        // whose address (a) embeds that token address in initcode and (b) has zero
        // flag bits. Token mining is cheap (no constraint), hook mining is the
        // 2^14 search.
        for (uint256 t = 0; t < 64; t++) {
            tokenSalt = bytes32(t);
            bytes memory tokenInit =
                abi.encodePacked(type(PhantomCreditToken).creationCode, abi.encode(address(0)));
            // token initcode depends on hook address -> iterate both together
            for (uint256 h = 0; h < 1_000_000; h++) {
                hookSalt = bytes32(h);
                // hook initcode depends on token address; token on hook. Break the
                // cycle: deploy token FIRST with hook=create2(hookAddr) once hookAddr
                // is known. So mine hook with token = f(hookAddr) unresolved ->
                // instead we fix token address by mining token salt against the
                // ALREADY-mined hook address in a second pass (done in run()).
                // Here we mine the hook with a dummy token; run() re-derives.
                bytes memory hookInit = abi.encodePacked(
                    type(MoebiusMEVHook).creationCode,
                    abi.encode(poolManager, address(0), treasury, minBidBps)
                );
                address predicted = _create2(CREATE2_DEPLOYER, hookSalt, keccak256(hookInit));
                if (uint160(predicted) & Hooks.ALL_HOOK_MASK == 0) {
                    hookAddr = predicted;
                    tokenAddr = _create2(
                        CREATE2_DEPLOYER,
                        tokenSalt,
                        keccak256(abi.encodePacked(type(PhantomCreditToken).creationCode, abi.encode(predicted)))
                    );
                    return (hookSalt, hookAddr, tokenSalt, tokenAddr);
                }
            }
        }
        revert("no salt found");
    }

    function _create2(address deployer_, bytes32 salt, bytes32 initHash) internal pure returns (address) {
        return address(uint160(uint256(keccak256(abi.encodePacked(bytes1(0xff), deployer_, salt, initHash)))));
    }
}
