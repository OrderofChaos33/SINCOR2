// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

/// @title P24 CreatorToken — fixed 1e9 supply, 50/50 curve/vesting, 5-yr vesting
/// @notice The factory is the only minter. Unvested creator tokens cannot be
///         sold or transferred (checked on every transfer).
/// @dev Self-contained for bare solc 0.8.24 compilation.
contract CreatorToken {
    string public name;
    string public symbol;
    uint8 public decimals = 18;
    uint256 public constant TOTAL_SUPPLY = 1_000_000_000 * 1e18; // 1e9, immutable
    uint256 public constant VESTING_SUPPLY = TOTAL_SUPPLY / 2;
    uint256 public constant VESTING_DURATION = 1825 days;

    address public immutable factory;
    address public immutable creator;
    uint64 public immutable issuedAt;
    string public policyVersion;

    mapping(address => uint256) public balanceOf;
    uint256 public transferredByCreator; // vested tokens moved out by creator

    event Transfer(address indexed from, address indexed to, uint256 amount);

    error OnlyFactory();
    error UnvestedTransfer();
    error InsufficientBalance();

    modifier onlyFactory() {
        if (msg.sender != factory) revert OnlyFactory();
        _;
    }

    constructor(
        string memory _name,
        string memory _symbol,
        address _creator,
        string memory _policyVersion,
        address _curveInventory
    ) {
        factory = msg.sender;
        name = _name;
        symbol = _symbol;
        creator = _creator;
        issuedAt = uint64(block.timestamp);
        policyVersion = _policyVersion;
        // 50% to the bonding-curve inventory, 50% to the vesting position.
        balanceOf[_curveInventory] = TOTAL_SUPPLY / 2;
        balanceOf[_creator] = VESTING_SUPPLY;
        emit Transfer(address(0), _curveInventory, TOTAL_SUPPLY / 2);
        emit Transfer(address(0), _creator, VESTING_SUPPLY);
    }

    function vestedAmount() public view returns (uint256) {
        uint256 elapsed = block.timestamp - issuedAt;
        if (elapsed >= VESTING_DURATION) return VESTING_SUPPLY;
        return VESTING_SUPPLY * elapsed / VESTING_DURATION;
    }

    function transferableByCreator() public view returns (uint256) {
        return vestedAmount() - transferredByCreator;
    }

    function transfer(address to, uint256 amount) external returns (bool) {
        if (balanceOf[msg.sender] < amount) revert InsufficientBalance();
        if (msg.sender == creator) {
            if (amount > transferableByCreator()) revert UnvestedTransfer();
            transferredByCreator += amount;
        }
        balanceOf[msg.sender] -= amount;
        balanceOf[to] += amount;
        emit Transfer(msg.sender, to, amount);
        return true;
    }

    /// @notice Factory-only mint is disabled post-construction: supply is fixed.
    function mint(address, uint256) external onlyFactory {
        revert("supply fixed at issuance");
    }
}

/// @title P24 CreatorTokenFactory — permissioned issuance, the only minter
contract CreatorTokenFactory {
    address public admin;
    mapping(string => address) public tokenBySymbol;

    event TokenIssued(address indexed token, string symbol, address indexed creator, string policyVersion);

    error NotAdmin();
    error SymbolTaken();
    error NotScreened();
    error ZeroCreator();

    constructor(address _admin) {
        admin = _admin;
    }

    /// @notice Issue a creator token. `screened` must be true: the offchain
    ///         content-policy guard (no_price_talk) runs before issuance and
    ///         its ruleset version is recorded on the token.
    function issue(
        string calldata name_,
        string calldata symbol_,
        address creator,
        address curveInventory,
        string calldata policyVersion,
        bool screened
    ) external returns (address token) {
        if (msg.sender != admin) revert NotAdmin();
        if (!screened) revert NotScreened();
        if (creator == address(0)) revert ZeroCreator();
        if (tokenBySymbol[symbol_] != address(0)) revert SymbolTaken();
        token = address(new CreatorToken(name_, symbol_, creator, policyVersion, curveInventory));
        tokenBySymbol[symbol_] = token;
        emit TokenIssued(token, symbol_, creator, policyVersion);
    }
}
