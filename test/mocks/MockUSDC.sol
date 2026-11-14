// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

/// @notice Test-only USDC stand-in (6 decimals, like USDC on Base) with a
///         public mint faucet. NOT production code.
///
/// @dev Deliberately a FALSE-RETURNING ERC-20, modelled on the sincor-x402
///      Python suite's fixture token but with legacy ERC-20 failure
///      semantics: `transferFrom`/`transfer` RETURN FALSE on insufficient
///      allowance/balance instead of reverting. That is the token behaviour
///      SincorSettlementHub's `if (!transferFrom(...)) revert
///      UsdcTransferFailed()` branch is written against, and it is the only
///      way that branch can fire. Tokens whose transferFrom REVERTS on
///      failure (e.g. OpenZeppelin ERC20) propagate their own revert through
///      the Hub instead — settlement still aborts and all state rolls back,
///      only the error differs. The unit tests pin the Hub-error path here;
///      the deploy package documents the distinction for production.
contract MockUSDC {
    string public constant name = "Mock USDC";
    string public constant symbol = "mUSDC";
    uint8 public constant decimals = 6;

    uint256 public totalSupply;
    mapping(address => uint256) public balanceOf;
    mapping(address => mapping(address => uint256)) public allowance;

    event Transfer(address indexed from, address indexed to, uint256 value);
    event Approval(address indexed owner, address indexed spender, uint256 value);

    function mint(address to, uint256 value) external {
        totalSupply += value;
        balanceOf[to] += value;
        emit Transfer(address(0), to, value);
    }

    function approve(address spender, uint256 value) external returns (bool) {
        allowance[msg.sender][spender] = value;
        emit Approval(msg.sender, spender, value);
        return true;
    }

    function transfer(address to, uint256 value) external returns (bool) {
        if (balanceOf[msg.sender] < value) return false;
        balanceOf[msg.sender] -= value;
        balanceOf[to] += value;
        emit Transfer(msg.sender, to, value);
        return true;
    }

    function transferFrom(address from, address to, uint256 value) external returns (bool) {
        uint256 allowed = allowance[from][msg.sender];
        if (allowed < value || balanceOf[from] < value) return false;
        if (allowed != type(uint256).max) {
            allowance[from][msg.sender] = allowed - value;
        }
        balanceOf[from] -= value;
        balanceOf[to] += value;
        emit Transfer(from, to, value);
        return true;
    }
}
