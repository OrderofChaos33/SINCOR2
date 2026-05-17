// SPDX-License-Identifier: MIT
pragma solidity 0.8.26;

import {ERC20} from "@openzeppelin/contracts/token/ERC20/ERC20.sol";

contract MockSinc is ERC20 {
    constructor(address recipient) ERC20("SINC", "SINC") {
        _mint(recipient, 100_000_000 * 10**8);
    }

    function decimals() public pure override returns (uint8) {
        return 8;
    }
}
