// SPDX-License-Identifier: MIT
pragma solidity ^0.8.0;

import "@openzeppelin/contracts/token/ERC20/ERC20.sol";

contract MockToken9 is ERC20 {
    constructor(string memory name, string memory symbol) ERC20(name, symbol) {
        // Mint a large initial supply to deployer for tests
        _mint(msg.sender, 1_000_000 * (10 ** 9));
    }

    function decimals() public pure override returns (uint8) {
        return 9;
    }
}
