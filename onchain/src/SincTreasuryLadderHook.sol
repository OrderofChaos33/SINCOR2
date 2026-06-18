// SPDX-License-Identifier: MIT
pragma solidity 0.8.26;

import {LimitOrderHook} from "@openzeppelin/uniswap-hooks/general/LimitOrderHook.sol";
import {BaseHook} from "@openzeppelin/uniswap-hooks/base/BaseHook.sol";
import {Hooks} from "@uniswap/v4-core/src/libraries/Hooks.sol";
import {IPoolManager} from "@uniswap/v4-core/src/interfaces/IPoolManager.sol";
import {PoolKey} from "@uniswap/v4-core/src/types/PoolKey.sol";
import {SwapParams} from "@uniswap/v4-core/src/types/PoolOperation.sol";
import {BeforeSwapDelta, toBeforeSwapDelta} from "@uniswap/v4-core/src/types/BeforeSwapDelta.sol";

/// @title SincTreasuryLadderHook
/// @notice V2 treasury hook: OZ limit-order book + anti-sandwich fees + batch ladder placement.
/// @dev The potent stack for SINC — park SINC in sell walls, buyers bring USDC, no LP capital needed.
///      Deploy via CREATE2 (mine 0x10C0 permission mask). Wire to curve via setHook for WETH graduation pool.
contract SincTreasuryLadderHook is LimitOrderHook {
    /// @notice Treasury receives protocol accounting; limit-order proceeds via withdraw().
    address public immutable treasury;

    /// @notice Per-pool swap counter for anti-sandwich fee scaling.
    mapping(bytes32 => mapping(uint256 => uint256)) public swapsInBlock;

    /// @notice Normal swap fee override: 0.30%
    uint24 public constant BASE_FEE = 3000;
    /// @notice Elevated fee on 2nd+ swap same block: 3.00% (sandwich disincentive)
    uint24 public constant SANDWICH_FEE = 30000;

    struct LadderRung {
        int24 tick;
        bool zeroForOne;
        uint128 liquidity;
    }

    event LadderPlaced(address indexed placer, uint256 rungCount);

    constructor(IPoolManager manager, address _treasury) BaseHook(manager) {
        require(_treasury != address(0), "zero treasury");
        treasury = _treasury;
    }

    /// @notice Place many limit-order rungs in one tx (gas-efficient ladder buildout).
    function placeLadder(PoolKey calldata key, LadderRung[] calldata rungs) external {
        uint256 n = rungs.length;
        for (uint256 i = 0; i < n; i++) {
            LadderRung calldata rung = rungs[i];
            placeOrder(key, rung.tick, rung.zeroForOne, rung.liquidity);
        }
        emit LadderPlaced(msg.sender, n);
    }

    /// @inheritdoc BaseHook
    function getHookPermissions() public pure override returns (Hooks.Permissions memory perms) {
        perms = super.getHookPermissions();
        perms.beforeSwap = true;
    }

    function _beforeSwap(address, PoolKey calldata key, SwapParams calldata, bytes calldata)
        internal
        override
        returns (bytes4, BeforeSwapDelta, uint24)
    {
        bytes32 pid = keccak256(abi.encode(key));
        uint256 count = swapsInBlock[pid][block.number];
        uint24 fee = count >= 1 ? SANDWICH_FEE : BASE_FEE;
        swapsInBlock[pid][block.number] = count + 1;
        return (this.beforeSwap.selector, toBeforeSwapDelta(0, 0), fee);
    }
}