// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {ERC20} from "@openzeppelin/contracts/token/ERC20/ERC20.sol";

contract PhantomCreditToken is ERC20 {
    address public immutable hook;

    error OnlyHook();

    modifier onlyHook() {
        if (msg.sender != hook) revert OnlyHook();
        _;
    }

    constructor(address _hook) ERC20("Phantom MEV Credit", "pMEV") {
        require(_hook != address(0), "Zero hook");
        hook = _hook;
    }

    function mintEphemeral(address to, uint256 amount) external onlyHook {
        _mint(to, amount);
    }

    function burnEphemeral(address from, uint256 amount) external onlyHook {
        _burn(from, amount);
    }
}