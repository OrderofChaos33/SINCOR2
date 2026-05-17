// SPDX-License-Identifier: MIT
pragma solidity 0.8.26;

import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {SincGenesisNFT} from "./SincGenesisNFT.sol";

contract SincBondingCurve {
    IERC20 public immutable sinc;
    address public immutable treasury;
    SincGenesisNFT public immutable nft;

    // Constant-product virtual reserves
    // Tuned so initial price (when 0 SINC sold) ≈ 3e-8 ETH per displayed SINC (≈ $0.0001 at ETH=$3000)
    uint256 public constant VIRTUAL_ETH = 3 ether;
    uint256 public constant VIRTUAL_SINC = 100_000_000 * 10**8;

    uint256 public sincSold;
    uint256 public ethAccumulated;
    bool public graduated;
    mapping(address => bool) public hasGenesisNFT;
    uint256 public nextBuyOrderNumber = 1;

    event Buy(address indexed buyer, uint256 ethIn, uint256 sincOut, address referrer);
    event Sell(address indexed seller, uint256 sincIn, uint256 ethOut);

    constructor(address _sinc, address _treasury, address _nft) {
        sinc = IERC20(_sinc);
        treasury = _treasury;
        nft = SincGenesisNFT(_nft);
    }

    function currentPriceWei() public view returns (uint256) {
        return (VIRTUAL_ETH + ethAccumulated) * 10**8 / (VIRTUAL_SINC - sincSold);
    }

    function getBuyAmount(uint256 ethIn) public view returns (uint256 sincOut) {
        uint256 k = (VIRTUAL_ETH + ethAccumulated) * (VIRTUAL_SINC - sincSold);
        uint256 newSincRemaining = k / (VIRTUAL_ETH + ethAccumulated + ethIn);
        uint256 rawOut = (VIRTUAL_SINC - sincSold) - newSincRemaining;
        // Subtract 1 atomic unit so the curve always captures the integer-division floor;
        // this guarantees buy→sell round-trips lose value (no lossless arbitrage).
        sincOut = rawOut > 0 ? rawOut - 1 : 0;
    }

    function getBuyCost(uint256 sincOut) public view returns (uint256 ethIn) {
        uint256 k = (VIRTUAL_ETH + ethAccumulated) * (VIRTUAL_SINC - sincSold);
        uint256 newEthVirtual = k / (VIRTUAL_SINC - sincSold - sincOut);
        ethIn = newEthVirtual - (VIRTUAL_ETH + ethAccumulated);
    }

    function getSellRefund(uint256 sincIn) public view returns (uint256 ethOut) {
        uint256 k = (VIRTUAL_ETH + ethAccumulated) * (VIRTUAL_SINC - sincSold);
        uint256 newEthVirtual = k / (VIRTUAL_SINC - sincSold + sincIn);
        ethOut = (VIRTUAL_ETH + ethAccumulated) - newEthVirtual;
    }

    function buy(uint256 ethIn, address referrer) external payable returns (uint256 sincOut) {
        require(!graduated, "Graduated");
        require(msg.value >= ethIn, "Insufficient ETH");
        require(ethIn > 0, "Zero ETH");

        // Referral split: 3% to referrer (or treasury fallback), 97% stays in curve
        uint256 referralCut = (ethIn * 3) / 100;
        uint256 curveCut = ethIn - referralCut;

        // Price SINC against the ETH that actually enters the curve (curveCut),
        // keeping the AMM invariant consistent with ethAccumulated.
        sincOut = getBuyAmount(curveCut);
        require(sincOut > 0, "Zero SINC out");
        require(sincOut <= sinc.balanceOf(address(this)), "Insufficient SINC in curve");

        address referralRecipient = (referrer != address(0) && referrer != msg.sender)
            ? referrer
            : treasury;

        sincSold += sincOut;
        ethAccumulated += curveCut;

        require(sinc.transfer(msg.sender, sincOut), "SINC transfer failed");

        // Auto-mint Genesis NFT on first buy only
        if (!hasGenesisNFT[msg.sender]) {
            hasGenesisNFT[msg.sender] = true;
            nft.mint(msg.sender, nextBuyOrderNumber++);
        }

        // Pay referrer (or treasury fallback)
        (bool refOk,) = referralRecipient.call{value: referralCut}("");
        require(refOk, "Referral payment failed");

        // Refund excess ETH
        if (msg.value > ethIn) {
            (bool ok,) = msg.sender.call{value: msg.value - ethIn}("");
            require(ok, "Refund failed");
        }

        emit Buy(msg.sender, ethIn, sincOut, referralRecipient);
    }

    function sell(uint256 sincIn) external returns (uint256 ethOut) {
        require(!graduated, "Graduated");
        require(sincIn > 0 && sincIn <= sincSold, "Sell exceeds amount sold");

        ethOut = getSellRefund(sincIn);
        require(address(this).balance >= ethOut, "Insufficient reserve");

        sincSold -= sincIn;
        ethAccumulated -= ethOut;

        require(sinc.transferFrom(msg.sender, address(this), sincIn), "SINC transferFrom failed");
        (bool ok,) = msg.sender.call{value: ethOut}("");
        require(ok, "ETH transfer failed");

        emit Sell(msg.sender, sincIn, ethOut);
    }

    receive() external payable {}
}
