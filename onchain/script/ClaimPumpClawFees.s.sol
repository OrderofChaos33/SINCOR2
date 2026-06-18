// SPDX-License-Identifier: MIT
pragma solidity 0.8.26;

import "forge-std/Script.sol";

interface IPumpClawLPLocker {
    function claimFees(address token) external;
    function getPosition(address token) external view returns (uint256 positionId, address creator);
}

/// @notice Claim accumulated Uniswap v4 LP fees for a PumpClaw-launched token.
/// @dev Fees split 80% creator / 20% admin. LP is permanently locked — this is the only recovery path.
///      Sign with the creator wallet (0x35cb3…) or any EOA; proceeds always route to the on-chain creator.
contract ClaimPumpClawFees is Script {
    address constant LOCKER = 0x9047c0944c843d91951a6C91dc9f3944D826ACA8;
    address constant AXM = 0xfF7aF6ffca25A9DC0FC990d998AcF24Cc60b7822;

    function run() external {
        uint256 signerKey = vm.envUint("DEPLOYER_PRIVATE_KEY");
        address token = vm.envOr("PUMPCLAW_TOKEN", AXM);

        (uint256 positionId, address creator) = IPumpClawLPLocker(LOCKER).getPosition(token);
        require(positionId != 0, "token not locked on PumpClaw");

        console.log("PumpClaw locker:", LOCKER);
        console.log("Token:", token);
        console.log("Position NFT:", positionId);
        console.log("Fee recipient (creator):", creator);

        vm.startBroadcast(signerKey);
        IPumpClawLPLocker(LOCKER).claimFees(token);
        vm.stopBroadcast();

        console.log("claimFees submitted - sweep ETH/USDC from creator to treasury after confirmation");
    }
}