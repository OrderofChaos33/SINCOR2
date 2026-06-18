// SPDX-License-Identifier: MIT
pragma solidity 0.8.26;

import "forge-std/Script.sol";

interface IERC20 {
    function balanceOf(address) external view returns (uint256);
    function transfer(address to, uint256 amount) external returns (bool);
}

/// @notice Sweep canonical SINC, legacy SINC, USDC, and ETH from compromised EOAs to SAFE_ADDRESS.
contract SweepToSafe is Script {
    address constant SINC = 0x9C8cd8d3961F445D653713dE65C6578bE11668e7;
    address constant LEGACY = 0x49E392de962Fa835B862F59E78611c69E930b5C4;
    address constant USDC = 0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913;

    function run() external {
        address safe = vm.envAddress("SAFE_ADDRESS");
        _sweepEoa("CREATOR_PRIVATE_KEY", safe);
        _sweepEoa("TREASURY_PRIVATE_KEY", safe);
        _sweepEoa("DEPLOYER_PRIVATE_KEY", safe);
    }

    function _sweepEoa(string memory keyName, address safe) internal {
        uint256 key = vm.envUint(keyName);
        address from = vm.addr(key);
        console.log("sweep from", from);

        vm.startBroadcast(key);
        uint256 ethBal = from.balance;
        if (ethBal > 0) {
            (bool ok,) = safe.call{value: ethBal}("");
            require(ok, "ETH sweep failed");
        }
        _transferAll(SINC, from, safe);
        _transferAll(LEGACY, from, safe);
        _transferAll(USDC, from, safe);
        vm.stopBroadcast();
    }

    function _transferAll(address token, address from, address to) internal {
        uint256 bal = IERC20(token).balanceOf(from);
        if (bal > 0) {
            require(IERC20(token).transfer(to, bal), "ERC20 sweep failed");
        }
    }
}