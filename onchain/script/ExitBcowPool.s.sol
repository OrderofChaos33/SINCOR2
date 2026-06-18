// SPDX-License-Identifier: MIT
pragma solidity 0.8.26;

import "forge-std/Script.sol";

interface IBCoWPool {
    function exitPool(uint256 poolAmountIn, uint256[] calldata minAmountsOut) external;
    function getFinalTokens() external view returns (address[] memory);
    function balanceOf(address account) external view returns (uint256);
    function approve(address spender, uint256 amount) external returns (bool);
}

/// @notice Redeem BCoW BPT from the Coinbase Smart Wallet (or any holder) via `exitPool`.
/// @dev Pool 0x6FDd… holds USDC + legacy SINC (0x49E3…), not canonical SINC.
///      Smart wallet 0x2a73… is pool controller and holds 100 BPT. Sign with CSW owner key.
contract ExitBcowPool is Script {
    address constant POOL = 0x6FDdC59f3a84E95685c6874D1Da45B3663b88E95;
    address constant SMART_WALLET = 0x2a73CCa8010b8A6b67bF86802D295ECf4Cf394b4;

    function run() external {
        uint256 signerKey = vm.envUint("DEPLOYER_PRIVATE_KEY");
        address holder = vm.envOr("BPT_HOLDER", SMART_WALLET);
        uint256 bptAmount = vm.envOr("BPT_AMOUNT", uint256(100 ether));

        uint256 held = IBCoWPool(POOL).balanceOf(holder);
        require(held > 0, "holder has no BPT");
        if (bptAmount > held) bptAmount = held;

        address[] memory tokens = IBCoWPool(POOL).getFinalTokens();
        uint256[] memory mins = new uint256[](tokens.length);

        console.log("BCoW pool:", POOL);
        console.log("BPT holder:", holder);
        console.log("Redeeming BPT:", bptAmount);
        for (uint256 i = 0; i < tokens.length; i++) {
            console.log("  token", i, tokens[i]);
        }

        require(holder == vm.addr(signerKey), "signer must hold BPT (use CSW owner key for 0x2a73)");

        vm.startBroadcast(signerKey);
        IBCoWPool(POOL).exitPool(bptAmount, mins);
        vm.stopBroadcast();

        console.log("exitPool submitted - sweep USDC/legacy SINC from holder to treasury");
    }
}