// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {ERC20} from "@openzeppelin/contracts/token/ERC20/ERC20.sol";

contract PhantomCreditToken is ERC20 {
    constructor(address _treasury) ERC20("Phantom Credit", "pCREDIT") {
        _mint(_treasury, 1_000_000_000 * 10**decimals());
    }
}
