// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {Script, console} from "forge-std/Script.sol";
import {SincorSettlementHub} from "../contracts/modules/SincorSettlementHub.sol";

interface IDrillUSDC {
    function approve(address spender, uint256 value) external returns (bool);
    function balanceOf(address account) external view returns (uint256);
}

/// @notice On-chain settle rehearsal for ceremony time (Base Sepolia first).
///         The one-shot deployer acts as the BUYER: it approves the Hub, signs
///         a SEAL in-script (vm.sign), and a settlement is submitted for a
///         small amount; provider/treasury deltas are asserted in-script.
///         This mirrors test/SincorSettlementHubIntegration.t.sol stage 5-6.
///
///         Broadcast ONLY by the founder at ceremony time with a fresh
///         one-shot wallet (see the deploy package runbook). Never broadcast
///         from this build session.
///
/// Env:
///   DEPLOYER_PRIVATE_KEY  Buyer/deployer key (shell env only, never a file).
///   HUB_ADDRESS           Deployed SincorSettlementHub address (required).
///   HUB_PROVIDER          Provider to pay. Default: a labelled drill address.
///   HUB_DRILL_AMOUNT      Seal amount in USDC atomic units. Default 1_000_000
///                         (1 Sepolia USDC). The buyer must actually hold it
///                         (Circle Base Sepolia faucet) — the drill settles
///                         real testnet funds, 95% / 5%.
contract SettleDrillSepolia is Script {
    address internal constant DEFAULT_PROVIDER = address(0xD2111);

    function run() external {
        uint256 buyerKey = vm.envOr("DEPLOYER_PRIVATE_KEY", uint256(0));
        require(
            buyerKey != 0,
            unicode"DEPLOYER_PRIVATE_KEY not set — export it in THIS shell session first (deploy package Sepolia step 4)"
        );
        address buyer = vm.addr(buyerKey);
        address hubAddr = vm.envAddress("HUB_ADDRESS");
        address provider = vm.envOr("HUB_PROVIDER", DEFAULT_PROVIDER);
        uint256 amount = vm.envOr("HUB_DRILL_AMOUNT", uint256(1_000_000));

        SincorSettlementHub hub = SincorSettlementHub(hubAddr);
        IDrillUSDC usdc = IDrillUSDC(address(hub.usdc()));
        address treasury = hub.feeTreasury();

        uint256 fee = (amount * hub.protocolFeeBps()) / 10_000;
        uint256 payout = amount - fee;

        uint256 providerBefore = usdc.balanceOf(provider);
        uint256 treasuryBefore = usdc.balanceOf(treasury);
        require(usdc.balanceOf(buyer) >= amount, "drill buyer underfunded (faucet USDC first)");

        // SEAL fields — SIGIL per the Hub-path canon:
        // keccak256(taskId || canonical-JSON output bytes).
        bytes32 taskId = keccak256(abi.encodePacked("sincor-settle-drill", block.chainid, buyer));
        bytes32 schemaHash = keccak256(bytes('{"type":"object","required":["result"]}'));
        bytes32 sigil = keccak256(abi.encodePacked(taskId, bytes('{"result":"drill-ok"}')));

        SincorSettlementHub.SealAuthorization memory seal = SincorSettlementHub.SealAuthorization({
            taskId: taskId,
            buyer: buyer,
            provider: provider,
            amount: amount,
            expectedSchemaHash: schemaHash,
            deadline: block.timestamp + 30 minutes,
            nonce: hub.nonces(buyer)
        });

        bytes32 structHash = keccak256(
            abi.encode(
                hub.SEAL_TYPEHASH(),
                seal.taskId,
                seal.buyer,
                seal.provider,
                seal.amount,
                seal.expectedSchemaHash,
                seal.deadline,
                seal.nonce
            )
        );
        bytes32 digest = keccak256(abi.encodePacked("\x19\x01", hub.DOMAIN_SEPARATOR(), structHash));
        (uint8 v, bytes32 r, bytes32 s) = vm.sign(buyerKey, digest);

        vm.startBroadcast(buyerKey);
        require(usdc.approve(hubAddr, amount), "drill approve failed");
        bool ok = hub.verifyAndSettleSeal(seal, sigil, v, r, s);
        vm.stopBroadcast();

        require(ok, "drill settle returned false");
        require(usdc.balanceOf(provider) == providerBefore + payout, "provider delta mismatch");
        require(usdc.balanceOf(treasury) == treasuryBefore + fee, "treasury delta mismatch");
        require(hub.isTaskVerified(taskId, sigil), "isTaskVerified false after drill");

        console.log("Settle drill OK. hub:", hubAddr);
        console.log("buyer:", buyer);
        console.log("provider payout:", payout);
        console.log("treasury fee:", fee);
    }
}
