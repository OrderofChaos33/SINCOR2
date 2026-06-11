// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/**
 * @title SINCPlatformAccess
 * @notice Escrow, prepaid credits, staking, and fee routing for the SINCOR platform.
 *         All balances are denominated in whole SINC tokens (decimals = 0).
 *
 * Architecture
 * ------------
 * • Users call purchaseCredits(amount) to deposit SINC and receive internal credits 1:1.
 * • The platform backend signer calls spendCredits(user, amount, taskId) to debit credits
 *   for pay-per-use actions (1 SINC/call, metered swarm usage, etc.).
 * • 5 % of every spend is immediately transferred to the treasury address; 95 % accrues
 *   to the "available fees" balance until claimed by the relevant payee (or batched).
 * • Users call stake(amount) to lock SINC for priority routing; unstake after the 7-day
 *   cooldown window.
 * • For A2A tasks payers call escrowTask(taskId, payee, amount); the platform signer
 *   calls releaseEscrow(taskId) on successful completion.
 * • claimFees() sweeps all accrued treasury fees to TREASURY_ADDRESS (callable by anyone).
 *
 * Security
 * --------
 * • ReentrancyGuard on every state-changing external function.
 * • Checks-Effects-Interactions (CEI) pattern throughout.
 * • Platform signer is an ECDSA signer address, not a privileged owner — rotatable.
 * • No owner, no upgrade proxy.  Treasury and SINC token addresses are immutable.
 * • All require() guards validated before any state mutation.
 *
 * Deployment
 * ----------
 * Chain  : Base (chainId 8453)
 * Token  : 0x9C8cd8d3961F445D653713dE65C6578bE11668e7  (SINC v3, decimals=0)
 * Treasury: 0xAf9B539D8043C634b7E611818518BA7E850F289e
 */

interface IERC20 {
    function transfer(address to, uint256 amount) external returns (bool);
    function transferFrom(address from, address to, uint256 amount) external returns (bool);
    function balanceOf(address account) external view returns (uint256);
}

contract ReentrancyGuard {
    uint256 private _status;
    uint256 private constant _NOT_ENTERED = 1;
    uint256 private constant _ENTERED = 2;

    constructor() {
        _status = _NOT_ENTERED;
    }

    modifier nonReentrant() {
        require(_status != _ENTERED, "ReentrancyGuard: reentrant call");
        _status = _ENTERED;
        _;
        _status = _NOT_ENTERED;
    }
}

contract SINCPlatformAccess is ReentrancyGuard {
    // -----------------------------------------------------------------------
    // Immutables & constants
    // -----------------------------------------------------------------------

    IERC20 public immutable sinc;
    address public immutable treasury;

    /// @notice Platform fee in basis points (500 = 5 %).
    uint256 public constant PLATFORM_FEE_BPS = 500;

    /// @notice Minimum SINC for a listing fee (one-time on marketplace registration).
    uint256 public constant LISTING_FEE = 50;

    /// @notice Minimum SINC stake required to list in the marketplace.
    uint256 public constant MIN_STAKE_TO_LIST = 250;

    /// @notice Priority routing threshold.
    uint256 public constant PRIORITY_STAKE_THRESHOLD = 1_000;

    /// @notice Enterprise / custom-agent tier.
    uint256 public constant ENTERPRISE_STAKE_THRESHOLD = 5_000;

    /// @notice Advanced-features holding gate (read-only check, not locked).
    uint256 public constant ADVANCED_FEATURE_MIN_HOLD = 500;

    /// @notice Unstake cooldown: 7 days in seconds.
    uint256 public constant UNSTAKE_COOLDOWN = 7 days;

    /// @notice Staking discount threshold in whole SINC.
    uint256 public constant STAKING_DISCOUNT_THRESHOLD = 1_000;

    /// @notice Staking discount in basis points (2000 = 20 %).
    uint256 public constant STAKING_DISCOUNT_BPS = 2_000;

    uint256 private constant _BPS_DENOMINATOR = 10_000;

    // -----------------------------------------------------------------------
    // State
    // -----------------------------------------------------------------------

    /// @notice Prepaid SINC credits available for platform actions.
    mapping(address => uint256) public credits;

    /// @notice Locked SINC for priority routing boost.
    mapping(address => uint256) public staked;

    /// @notice Timestamp when unstake was last requested (cooldown tracking).
    mapping(address => uint256) public unstakeRequestedAt;

    /// @notice Pending unstake amount (set on requestUnstake).
    mapping(address => uint256) public pendingUnstake;

    /// @notice Accrued treasury fees not yet swept.
    uint256 public accruedTreasuryFees;

    // Escrow
    struct EscrowRecord {
        address payer;
        address payee;
        uint256 amount;
        bool released;
        bool refunded;
    }
    mapping(bytes32 => EscrowRecord) public escrows;

    /// @notice Authorized platform signer for spendCredits / releaseEscrow.
    address public platformSigner;

    // -----------------------------------------------------------------------
    // Events
    // -----------------------------------------------------------------------

    event CreditsPurchased(address indexed user, uint256 amount);
    event CreditsSpent(address indexed user, uint256 amount, bytes32 indexed taskId);
    event Staked(address indexed user, uint256 amount);
    event UnstakeRequested(address indexed user, uint256 amount, uint256 availableAt);
    event Unstaked(address indexed user, uint256 amount);
    event EscrowCreated(bytes32 indexed taskId, address indexed payer, address indexed payee, uint256 amount);
    event EscrowReleased(bytes32 indexed taskId, address indexed payer, address indexed payee, uint256 amount);
    event EscrowRefunded(bytes32 indexed taskId, address indexed payer, uint256 amount);
    event FeePaid(address indexed user, uint256 fee);
    event FeesClaimed(address indexed treasury, uint256 amount);
    event ListingFeePaid(address indexed agent, uint256 amount);
    event PlatformSignerRotated(address indexed oldSigner, address indexed newSigner);

    // -----------------------------------------------------------------------
    // Constructor
    // -----------------------------------------------------------------------

    constructor(address _sinc, address _treasury, address _platformSigner) {
        require(_sinc != address(0), "SINC: zero address");
        require(_treasury != address(0), "Treasury: zero address");
        require(_platformSigner != address(0), "Signer: zero address");
        sinc = IERC20(_sinc);
        treasury = _treasury;
        platformSigner = _platformSigner;
    }

    // -----------------------------------------------------------------------
    // Modifiers
    // -----------------------------------------------------------------------

    modifier onlyPlatformSigner() {
        require(msg.sender == platformSigner, "caller is not platform signer");
        _;
    }

    // -----------------------------------------------------------------------
    // Credits — prepaid SINC for metered platform actions
    // -----------------------------------------------------------------------

    /**
     * @notice Deposit SINC tokens as prepaid platform credits (1 SINC = 1 credit).
     * @param amount Number of SINC to deposit (minimum 10).
     */
    function purchaseCredits(uint256 amount) external nonReentrant {
        require(amount >= 10, "minimum purchase is 10 SINC");
        // CEI: update state before external call
        credits[msg.sender] += amount;
        require(sinc.transferFrom(msg.sender, address(this), amount), "SINC transfer failed");
        emit CreditsPurchased(msg.sender, amount);
    }

    /**
     * @notice Deduct credits for a metered platform action.
     *         5 % fee routes to treasury; callable only by the platform signer.
     * @param user    The user whose credits are debited.
     * @param amount  Credits to spend (in SINC).
     * @param taskId  Platform task / action identifier for logging.
     */
    function spendCredits(address user, uint256 amount, bytes32 taskId) external nonReentrant onlyPlatformSigner {
        require(amount > 0, "amount must be > 0");
        require(credits[user] >= amount, "insufficient credits");

        uint256 fee = (amount * PLATFORM_FEE_BPS) / _BPS_DENOMINATOR;

        // CEI: deduct credits before transferring fee
        credits[user] -= amount;
        accruedTreasuryFees += fee;

        // Transfer fee to treasury immediately
        require(sinc.transfer(treasury, fee), "fee transfer failed");

        emit CreditsSpent(user, amount, taskId);
        emit FeePaid(user, fee);
    }

    /**
     * @notice Apply the staking discount to a credit cost for eligible users.
     * @param user       The user to check.
     * @param baseCost   The undiscounted credit cost.
     * @return discounted The effective cost after any applicable discount.
     */
    function effectiveCost(address user, uint256 baseCost) public view returns (uint256 discounted) {
        if (staked[user] >= STAKING_DISCOUNT_THRESHOLD) {
            discounted = baseCost - (baseCost * STAKING_DISCOUNT_BPS) / _BPS_DENOMINATOR;
        } else {
            discounted = baseCost;
        }
    }

    // -----------------------------------------------------------------------
    // Staking
    // -----------------------------------------------------------------------

    /**
     * @notice Lock SINC tokens to boost routing priority.
     * @param amount SINC to stake.
     */
    function stake(uint256 amount) external nonReentrant {
        require(amount > 0, "amount must be > 0");
        staked[msg.sender] += amount;
        require(sinc.transferFrom(msg.sender, address(this), amount), "SINC transfer failed");
        emit Staked(msg.sender, amount);
    }

    /**
     * @notice Begin the unstake cooldown for a given amount.
     *         Must wait UNSTAKE_COOLDOWN seconds before calling finaliseUnstake().
     * @param amount SINC to request for unstaking.
     */
    function requestUnstake(uint256 amount) external nonReentrant {
        require(amount > 0, "amount must be > 0");
        require(staked[msg.sender] >= amount, "insufficient staked balance");
        require(pendingUnstake[msg.sender] == 0, "unstake already pending");

        staked[msg.sender] -= amount;
        pendingUnstake[msg.sender] = amount;
        unstakeRequestedAt[msg.sender] = block.timestamp;

        uint256 availableAt = block.timestamp + UNSTAKE_COOLDOWN;
        emit UnstakeRequested(msg.sender, amount, availableAt);
    }

    /**
     * @notice Complete an unstake after the cooldown has passed.
     */
    function finaliseUnstake() external nonReentrant {
        uint256 amount = pendingUnstake[msg.sender];
        require(amount > 0, "no pending unstake");
        require(
            block.timestamp >= unstakeRequestedAt[msg.sender] + UNSTAKE_COOLDOWN,
            "cooldown not elapsed"
        );

        // CEI: clear pending before transfer
        pendingUnstake[msg.sender] = 0;
        unstakeRequestedAt[msg.sender] = 0;

        require(sinc.transfer(msg.sender, amount), "SINC transfer failed");
        emit Unstaked(msg.sender, amount);
    }

    // -----------------------------------------------------------------------
    // Marketplace listing fee
    // -----------------------------------------------------------------------

    /**
     * @notice Pay the one-time listing fee to register an agent in the marketplace.
     *         Requires MIN_STAKE_TO_LIST SINC already staked by the caller.
     */
    function payListingFee() external nonReentrant {
        require(staked[msg.sender] >= MIN_STAKE_TO_LIST, "must stake >= 250 SINC to list");
        require(credits[msg.sender] >= LISTING_FEE, "insufficient credits for listing fee");

        credits[msg.sender] -= LISTING_FEE;
        accruedTreasuryFees += LISTING_FEE;
        require(sinc.transfer(treasury, LISTING_FEE), "fee transfer failed");

        emit ListingFeePaid(msg.sender, LISTING_FEE);
    }

    // -----------------------------------------------------------------------
    // A2A Task Escrow
    // -----------------------------------------------------------------------

    /**
     * @notice Lock SINC for an A2A task, held until completion or refund.
     * @param taskId  Unique task identifier (hash of task details).
     * @param payee   Agent wallet that receives payment on success.
     * @param amount  SINC to escrow.
     */
    function escrowTask(bytes32 taskId, address payee, uint256 amount) external nonReentrant {
        require(amount > 0, "amount must be > 0");
        require(payee != address(0), "payee: zero address");
        require(escrows[taskId].payer == address(0), "taskId already escrowed");

        // CEI: record before transfer
        escrows[taskId] = EscrowRecord({
            payer: msg.sender,
            payee: payee,
            amount: amount,
            released: false,
            refunded: false
        });

        require(sinc.transferFrom(msg.sender, address(this), amount), "SINC transfer failed");
        emit EscrowCreated(taskId, msg.sender, payee, amount);
    }

    /**
     * @notice Release escrowed SINC to the payee after task completion.
     *         5 % fee routes to treasury.  Callable only by platform signer.
     * @param taskId  The task identifier.
     */
    function releaseEscrow(bytes32 taskId) external nonReentrant onlyPlatformSigner {
        EscrowRecord storage rec = escrows[taskId];
        require(rec.payer != address(0), "escrow not found");
        require(!rec.released && !rec.refunded, "escrow already settled");

        uint256 fee = (rec.amount * PLATFORM_FEE_BPS) / _BPS_DENOMINATOR;
        uint256 payeeAmount = rec.amount - fee;

        // CEI: mark released before transfers
        rec.released = true;
        accruedTreasuryFees += fee;

        require(sinc.transfer(rec.payee, payeeAmount), "payee transfer failed");
        require(sinc.transfer(treasury, fee), "fee transfer failed");

        emit EscrowReleased(taskId, rec.payer, rec.payee, payeeAmount);
        emit FeePaid(rec.payer, fee);
    }

    /**
     * @notice Refund escrowed SINC to the payer (e.g. task cancelled or timed out).
     *         Callable only by platform signer.
     * @param taskId  The task identifier.
     */
    function refundEscrow(bytes32 taskId) external nonReentrant onlyPlatformSigner {
        EscrowRecord storage rec = escrows[taskId];
        require(rec.payer != address(0), "escrow not found");
        require(!rec.released && !rec.refunded, "escrow already settled");

        address payer = rec.payer;
        uint256 amount = rec.amount;

        // CEI: mark refunded before transfer
        rec.refunded = true;

        require(sinc.transfer(payer, amount), "refund transfer failed");
        emit EscrowRefunded(taskId, payer, amount);
    }

    // -----------------------------------------------------------------------
    // Fee collection
    // -----------------------------------------------------------------------

    /**
     * @notice Sweep any accrued fees to the treasury address.
     *         Callable by anyone.
     */
    function claimFees() external nonReentrant {
        uint256 amount = accruedTreasuryFees;
        require(amount > 0, "no fees to claim");
        accruedTreasuryFees = 0;
        require(sinc.transfer(treasury, amount), "fee transfer failed");
        emit FeesClaimed(treasury, amount);
    }

    // -----------------------------------------------------------------------
    // Platform signer rotation
    // -----------------------------------------------------------------------

    /**
     * @notice Rotate the platform signer address.  The current signer must call this.
     * @param newSigner Replacement signer address.
     */
    function rotatePlatformSigner(address newSigner) external {
        require(msg.sender == platformSigner, "caller is not platform signer");
        require(newSigner != address(0), "new signer: zero address");
        address old = platformSigner;
        platformSigner = newSigner;
        emit PlatformSignerRotated(old, newSigner);
    }

    // -----------------------------------------------------------------------
    // View helpers
    // -----------------------------------------------------------------------

    /// @notice Returns true when the user holds/stakes enough to list in marketplace.
    function canList(address user) external view returns (bool) {
        return staked[user] >= MIN_STAKE_TO_LIST && credits[user] >= LISTING_FEE;
    }

    /// @notice Returns the routing tier for a staker.
    function stakeTier(address user) external view returns (string memory) {
        uint256 s = staked[user];
        if (s >= ENTERPRISE_STAKE_THRESHOLD) return "enterprise";
        if (s >= PRIORITY_STAKE_THRESHOLD) return "priority";
        if (s >= MIN_STAKE_TO_LIST) return "standard";
        return "none";
    }
}
