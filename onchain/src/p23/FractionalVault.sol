// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {CollectionRegistry} from "./CollectionRegistry.sol";
import {NftPricingOracle} from "./NftPricingOracle.sol";

/// @title P23 FractionalVault — fractional NFT shares with utilization cap
/// @notice Per-collection vault: locks ERC-721s (via safeTransferFrom custody
///         hooks on the NFT contract side), mints ERC-20 fractional shares at
///         oracle NAV, enforces the 0.75 utilization cap on the lending
///         sleeve, keeps a 5% reserve sleeve as the default backstop.
///         Withdrawals are never blocked by the utilization cap.
/// @dev Self-contained for bare solc 0.8.24 compilation. Minimal ERC-20 share
///      implementation inline; production would use OZ ERC20 + ReentrancyGuard.
contract FractionalVault {
    uint256 public constant BPS = 10_000;
    uint256 public constant UTILIZATION_CAP_BPS = 7500; // 0.75
    uint256 public constant DEPOSIT_FEE_BPS = 50;       // 0.5%
    uint256 public constant RESERVE_SLEEVE_BPS = 500;    // 5%
    uint256 public constant MINIMUM_SHARES = 1_000;     // burn-address guard
    address public constant BURN = address(0x0000000000000000000000000000000000000001);

    CollectionRegistry public immutable registry;
    NftPricingOracle public immutable oracle;
    address public immutable collection; // the ERC-721 collection for this vault

    // --- minimal ERC-20 shares ---
    string public name = "P23 Fractional Share";
    string public symbol = "P23F";
    uint8 public decimals = 18;
    uint256 public totalSupply;
    mapping(address => uint256) public balanceOf;

    // --- pool accounting (USDC-wei, 6dp) ---
    uint256 public poolValue;    // NAV incl. accrued interest
    uint256 public deployed;     // lending sleeve
    uint256 public reserve;      // backstop sleeve accounting
    uint256 private _locked;     // reentrancy guard

    event Deposit(address indexed owner, uint256 indexed tokenId, uint256 shares, uint256 floorPrice);
    event Redeem(address indexed owner, uint256 shares, uint256 payout);
    event Deployed(uint256 amount, uint256 utilizationBps);
    event DefaultAbsorbed(uint256 value);

    error NotWhitelisted();
    error DepositsFrozen();
    error NoPrice();
    error CapBreached();
    error InsufficientShares();
    error Reentrant();

    modifier nonReentrant() {
        if (_locked == 1) revert Reentrant();
        _locked = 1;
        _;
        _locked = 0;
    }

    constructor(CollectionRegistry _registry, NftPricingOracle _oracle, address _collection) {
        registry = _registry;
        oracle = _oracle;
        collection = _collection;
    }

    function sharePrice() public view returns (uint256) {
        if (totalSupply == 0) return 1; // 1 wei per share pre-first-deposit
        return poolValue * 1e18 / totalSupply;
    }

    function utilizationBps() public view returns (uint256) {
        if (poolValue == 0) return 0;
        return deployed * BPS / poolValue;
    }

    /// @notice Deposit an NFT (custody transfer happens via the collection's
    ///         safeTransferFrom to this vault before/after this call in the
    ///         integration flow). Mints fractional shares at oracle NAV.
    function deposit(address owner, uint256 tokenId) external nonReentrant returns (uint256 shares) {
        if (!registry.isWhitelisted(collection)) revert NotWhitelisted();
        if (oracle.depositsFrozen(collection)) revert DepositsFrozen();
        uint256 floorPrice = oracle.floorPrice(collection);
        if (floorPrice == 0) revert NoPrice();
        uint256 netValue = floorPrice * (BPS - DEPOSIT_FEE_BPS) / BPS;
        uint256 price = sharePrice(); // USDC-wei per share, 1e18-scaled
        shares = netValue * 1e18 / price;
        require(shares > 0, "dust");
        if (totalSupply == 0) {
            totalSupply = MINIMUM_SHARES; // inflation-attack guard
            balanceOf[BURN] = MINIMUM_SHARES;
        }
        totalSupply += shares;
        balanceOf[owner] += shares;
        poolValue += netValue;
        emit Deposit(owner, tokenId, shares, floorPrice);
    }

    /// @notice Move pool value into the lending sleeve. Reverts on cap breach.
    ///         Withdrawals never call this path, so they are never blocked.
    function deployToLending(uint256 amount) external nonReentrant {
        uint256 newDeployed = deployed + amount;
        if (poolValue == 0 || newDeployed * BPS / poolValue > UTILIZATION_CAP_BPS) revert CapBreached();
        deployed = newDeployed;
        emit Deployed(amount, utilizationBps());
    }

    function repayToPool(uint256 amount) external nonReentrant {
        require(amount <= deployed, "over-repay");
        deployed -= amount;
    }

    /// @notice Borrower interest accrues to share NAV.
    function accrueInterest(uint256 interest) external nonReentrant {
        poolValue += interest;
    }

    /// @notice Burn shares for pro-rata underlying value. Never cap-blocked.
    function redeem(uint256 shares) external nonReentrant returns (uint256 payout) {
        if (balanceOf[msg.sender] < shares || shares == 0) revert InsufficientShares();
        payout = shares * poolValue / totalSupply; // pro-rata USDC-wei
        balanceOf[msg.sender] -= shares;
        totalSupply -= shares;
        poolValue -= payout;
        if (deployed > poolValue) deployed = poolValue;
        emit Redeem(msg.sender, shares, payout);
    }

    /// @notice Reserve-sleeve backstop: absorb defaulted collateral value.
    function absorbDefault(uint256 collateralValue) external nonReentrant returns (uint256 absorbed) {
        uint256 sleeve = poolValue * RESERVE_SLEEVE_BPS / BPS;
        absorbed = collateralValue > sleeve ? sleeve : collateralValue;
        reserve += absorbed;
        emit DefaultAbsorbed(absorbed);
    }
}
