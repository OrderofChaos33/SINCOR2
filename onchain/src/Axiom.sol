// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/**
 * @title AXIOM Token (AXM)
 * @notice The autonomous intelligence token of the SINCOR ecosystem on Base.
 *
 *   Symbol  : AXM
 *   Supply  : 1,000,000,000 (1 billion, fixed)
 *   Decimals: 18
 *   Chain   : Base (chainId 8453)
 *   Address : 0xfF7aF6ffca25A9DC0FC990d998AcF24Cc60b7822
 *
 * Design constraints (identical to SINC v3):
 *   - No owner / admin role
 *   - No mint function
 *   - No pause / blacklist / honeypot / tax
 *   - No proxy, no self-destruct
 *   - Standard ERC-20: transfer, approve, transferFrom
 *
 * Role in the SINCOR ecosystem:
 *   AXIOM is the "oil in the engine" for agent-to-agent (A2A) interactions.
 *   External compliant agents (Hermes, Claude, OpenAI-compatible, etc.)
 *   acquire AXM to invoke SINCOR agents, and SINCOR agents earn AXM for
 *   fulfilled tasks. 80% of AXM trading fees are routed (off-chain commitment,
 *   on-chain auditable via the team wallet) back into the SINCOR ecosystem
 *   treasury.
 */
contract Axiom {
    string public constant name     = "AXIOM";
    string public constant symbol   = "AXM";
    uint8  public constant decimals = 18;

    uint256 public totalSupply;
    mapping(address => uint256) public balanceOf;
    mapping(address => mapping(address => uint256)) public allowance;

    event Transfer(address indexed from, address indexed to, uint256 value);
    event Approval(address indexed owner, address indexed spender, uint256 value);

    constructor(address _treasury) {
        totalSupply = 1_000_000_000 * 10 ** uint256(decimals);
        balanceOf[_treasury] = totalSupply;
        emit Transfer(address(0), _treasury, totalSupply);
    }

    function transfer(address to, uint256 value) external returns (bool) {
        require(to != address(0), "zero address");
        require(balanceOf[msg.sender] >= value, "insufficient balance");
        balanceOf[msg.sender] -= value;
        balanceOf[to] += value;
        emit Transfer(msg.sender, to, value);
        return true;
    }

    function approve(address spender, uint256 value) external returns (bool) {
        allowance[msg.sender][spender] = value;
        emit Approval(msg.sender, spender, value);
        return true;
    }

    function transferFrom(address from, address to, uint256 value) external returns (bool) {
        require(to != address(0), "zero address");
        require(balanceOf[from] >= value, "insufficient balance");
        require(allowance[from][msg.sender] >= value, "insufficient allowance");
        allowance[from][msg.sender] -= value;
        balanceOf[from] -= value;
        balanceOf[to] += value;
        emit Transfer(from, to, value);
        return true;
    }
}
