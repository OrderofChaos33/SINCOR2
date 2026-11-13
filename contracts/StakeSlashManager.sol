// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

/**
 * @title StakeSlashManager
 * @notice On-chain stake deposits and adjudicator-ruled slashing for the
 *         SINCOR2 sealed-bid task market. Python accounting lives in
 *         src/sincor2/onchain/stake_ledger.py; this contract is the money
 *         layer it reconciles against.
 *
 * @dev Security properties:
 *      - Adjudicator pattern mirrors ExecutionEscrowManager: a single
 *        `adjudicator` address, `onlyAdjudicator` modifier, and
 *        `setAdjudicator` rotation (zero-address guarded). The demo
 *        adjudicator is a founder-held EOA; the mainnet design reserves a
 *        decentralized adjudicator.
 *      - Slash rulings are EIP-191 signatures over a domain-bound struct
 *        hash (contract address + chainid), so a ruling cannot be replayed
 *        on another chain or another deployment. Per-agent nonces stop
 *        ruling replay; expiries bound the replay window.
 *      - ECDSA malleability guard: s must be in the lower half-order and
 *        v must be 27/28 (a malleability bypass was found and fixed in the
 *        Python caller-ownership layer; the same class is closed here).
 *      - Checks-Effects-Interactions: stake balances and nonces are updated
 *        BEFORE any external token transfer.
 *      - Slash proceeds follow the ratified subsidy-extraction invariant:
 *        the platform's unrecouped sponsored front (`treasuryCut`,
 *        computed off-chain by the Python ledger which knows the front)
 *        goes to the platform treasury first as senior creditor; the
 *        remainder becomes poster re-auction CREDIT (ledger-only, 1:1
 *        backed by tokens held in this contract -- same pattern as
 *        ExecutionEscrowManager.posterReAuctionBalances). Drawing credits
 *        down is a follow-up (poster fast-path deferred).
 *      - Unstake is request/finalize with a 7-day timelock, mirroring the
 *        KYA clean-exit timelock: a rage-quit cannot dodge a pending
 *        ruling. An open dispute hold is enforced off-chain by the Python
 *        layer refusing to request unstake while disputed.
 *      - Unstake/slash interaction (W-33): slash() executes IMMEDIATELY on
 *        a valid adjudicator signature (there is no staged
 *        propose/appeal/execute flow in this contract), so a slash can
 *        legitimately land inside the 7-day unstake timelock. finalizeUnstake
 *        therefore finalizes min(pending, stakeOf) -- a slash-shrunk
 *        balance clamps instead of reverting, so funds can never be
 *        bricked, and the Unstaked event reports the actual amount paid.
 */
contract StakeSlashManager {
    // --- types ------------------------------------------------------------
    bytes32 public constant REASON_GHOST = keccak256("ghost");
    bytes32 public constant REASON_QUALITY = keccak256("quality");
    bytes32 private constant RULING_TYPEHASH = keccak256("SINCOR-SLASH");

    // secp256k1 curve order / 2 -- malleability guard bound.
    // n = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141
    uint256 private constant HALF_N =
        0x7FFFFFFFFFFFFFFFFFFFFFFFFFFFFFFF5D576E7357A4501DDFE92F46681B20A0;

    // --- errors -----------------------------------------------------------
    error Unauthorized();
    error InvalidConfig();
    error InvalidAmount();
    error InsufficientStake();
    error BadNonce(uint256 expected, uint256 got);
    error Expired(uint256 expiry, uint256 now_);
    error BadReason();
    error BadSignature();
    error NoPendingUnstake();
    error TimelockNotElapsed(uint256 readyAt, uint256 now_);

    // --- events -----------------------------------------------------------
    event Staked(address indexed agent, uint256 amount);
    event UnstakeRequested(address indexed agent, uint256 amount, uint256 readyAt);
    event Unstaked(address indexed agent, uint256 amount);
    event SlashExecuted(
        address indexed agent,
        address indexed poster,
        uint256 amount,
        uint256 treasuryCut,
        uint64 nonce,
        bytes32 reason
    );
    event AdjudicatorRotated(address indexed oldAdjudicator, address indexed newAdjudicator);
    event MinStakeUpdated(uint256 minStakeWei);

    // --- storage ----------------------------------------------------------
    address public immutable admin;
    address public immutable collateralToken;
    address public immutable platformTreasury;
    uint256 public immutable unstakeTimelock;
    address public adjudicator;
    uint256 public minStakeWei;

    mapping(address => uint256) public stakeOf;
    mapping(address => uint64) public slashNonceOf;
    mapping(address => uint256) public posterReAuctionCredits;
    mapping(address => uint256) public unstakeReadyAt;
    mapping(address => uint256) public unstakePending;

    modifier onlyAdmin() {
        if (msg.sender != admin) revert Unauthorized();
        _;
    }

    modifier onlyAdjudicator() {
        if (msg.sender != adjudicator) revert Unauthorized();
        _;
    }

    constructor(
        address _admin,
        address _adjudicator,
        address _collateralToken,
        address _platformTreasury,
        uint256 _minStakeWei,
        uint256 _unstakeTimelock
    ) {
        if (
            _admin == address(0) ||
            _adjudicator == address(0) ||
            _collateralToken == address(0) ||
            _platformTreasury == address(0)
        ) revert InvalidConfig();
        admin = _admin;
        adjudicator = _adjudicator;
        collateralToken = _collateralToken;
        platformTreasury = _platformTreasury;
        minStakeWei = _minStakeWei;
        unstakeTimelock = _unstakeTimelock == 0 ? 7 days : _unstakeTimelock;
    }

    // --- admin ------------------------------------------------------------
    function setAdjudicator(address newAdjudicator) external onlyAdjudicator {
        if (newAdjudicator == address(0)) revert InvalidConfig();
        emit AdjudicatorRotated(adjudicator, newAdjudicator);
        adjudicator = newAdjudicator;
    }

    function setMinStakeWei(uint256 _minStakeWei) external onlyAdmin {
        minStakeWei = _minStakeWei;
        emit MinStakeUpdated(_minStakeWei);
    }

    // --- staking ----------------------------------------------------------
    function stake(uint256 amount) external {
        if (amount == 0) revert InvalidAmount();
        stakeOf[msg.sender] += amount;
        emit Staked(msg.sender, amount);
        _safeTransferFrom(msg.sender, address(this), amount);
    }

    function requestUnstake(uint256 amount) external {
        uint256 current = stakeOf[msg.sender];
        if (amount == 0 || amount > current) revert InsufficientStake();
        // Cannot dip below minStake unless exiting fully.
        if (current - amount < minStakeWei && current != amount) revert InsufficientStake();
        unstakePending[msg.sender] = amount;
        unstakeReadyAt[msg.sender] = block.timestamp + unstakeTimelock;
        emit UnstakeRequested(msg.sender, amount, block.timestamp + unstakeTimelock);
    }

    function finalizeUnstake() external {
        uint256 amount = unstakePending[msg.sender];
        if (amount == 0) revert NoPendingUnstake();
        uint256 readyAt = unstakeReadyAt[msg.sender];
        if (block.timestamp < readyAt) revert TimelockNotElapsed(readyAt, block.timestamp);
        uint256 current = stakeOf[msg.sender];
        // W-33: an adjudicator slash may land between requestUnstake and
        // finalizeUnstake, shrinking stakeOf below the requested amount.
        // Reverting here bricks the withdrawal (funds stuck forever).
        // Clamp to what is actually there: the user always gets the true
        // remainder, and the emitted event reports the ACTUAL finalized
        // amount, never the (possibly larger) requested amount.
        if (amount > current) {
            amount = current;
        }
        unstakePending[msg.sender] = 0;
        unstakeReadyAt[msg.sender] = 0;
        stakeOf[msg.sender] = current - amount;
        emit Unstaked(msg.sender, amount);
        _safeTransfer(msg.sender, amount);
    }

    function canParticipate(address agent) external view returns (bool) {
        return stakeOf[agent] >= minStakeWei;
    }

    // --- slashing ---------------------------------------------------------
    /**
     * @notice Execute an adjudicator-signed slash ruling. Permissionless to
     *         call; the ruling's signature is the authorization.
     * @param agent      Staker being slashed.
     * @param poster     Poster whose re-auction fund receives the credit.
     * @param amount     Total wei to slash (<= stakeOf[agent]).
     * @param treasuryCut Wei of `amount` reclaimed to the platform treasury
     *                   (sponsored-front clawback, computed off-chain).
     * @param nonce      Must equal slashNonceOf[agent]; consumed on success.
     * @param expiry     Unix timestamp after which the ruling is void.
     * @param reason     REASON_GHOST or REASON_QUALITY.
     */
    function slash(
        address agent,
        address poster,
        uint256 amount,
        uint256 treasuryCut,
        uint64 nonce,
        uint64 expiry,
        bytes32 reason,
        uint8 v,
        bytes32 r,
        bytes32 s
    ) external {
        if (agent == address(0) || poster == address(0)) revert InvalidConfig();
        if (amount == 0 || amount > stakeOf[agent]) revert InsufficientStake();
        if (treasuryCut > amount) revert InvalidAmount();
        if (reason != REASON_GHOST && reason != REASON_QUALITY) revert BadReason();
        if (block.timestamp > expiry) revert Expired(expiry, block.timestamp);
        uint64 expected = slashNonceOf[agent];
        if (nonce != expected) revert BadNonce(expected, nonce);

        bytes32 structHash = keccak256(
            abi.encode(
                RULING_TYPEHASH,
                block.chainid,
                address(this),
                agent,
                poster,
                amount,
                treasuryCut,
                nonce,
                expiry,
                reason
            )
        );
        bytes32 ethSigned = keccak256(
            abi.encodePacked("\x19Ethereum Signed Message:\n32", structHash)
        );
        address signer = _recover(ethSigned, v, r, s);
        if (signer != adjudicator) revert BadSignature();

        // Effects before interactions.
        slashNonceOf[agent] = expected + 1;
        stakeOf[agent] -= amount;
        uint256 credit = amount - treasuryCut;
        if (credit > 0) {
            posterReAuctionCredits[poster] += credit;
        }
        emit SlashExecuted(agent, poster, amount, treasuryCut, nonce, reason);

        if (treasuryCut > 0) {
            _safeTransfer(platformTreasury, treasuryCut);
        }
    }

    // --- internals --------------------------------------------------------
    function _recover(
        bytes32 digest,
        uint8 v,
        bytes32 r,
        bytes32 s
    ) internal pure returns (address) {
        if (v != 27 && v != 28) revert BadSignature();
        if (uint256(s) > HALF_N) revert BadSignature();
        if (r == bytes32(0) || s == bytes32(0)) revert BadSignature();
        address signer = ecrecover(digest, v, r, s);
        if (signer == address(0)) revert BadSignature();
        return signer;
    }

    function _safeTransfer(address to, uint256 amount) internal {
        (bool ok, bytes memory ret) = collateralToken.call(
            abi.encodeWithSignature("transfer(address,uint256)", to, amount)
        );
        if (!ok || (ret.length > 0 && !abi.decode(ret, (bool)))) revert InvalidAmount();
    }

    function _safeTransferFrom(address from, address to, uint256 amount) internal {
        (bool ok, bytes memory ret) = collateralToken.call(
            abi.encodeWithSignature("transferFrom(address,address,uint256)", from, to, amount)
        );
        if (!ok || (ret.length > 0 && !abi.decode(ret, (bool)))) revert InvalidAmount();
    }
}
