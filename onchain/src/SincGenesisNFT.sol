// SPDX-License-Identifier: MIT
pragma solidity 0.8.26;

import {ERC721} from "@openzeppelin/contracts/token/ERC721/ERC721.sol";

contract SincGenesisNFT is ERC721 {
    address public immutable curve;
    uint256 public nextTokenId = 1;

    event GenesisMinted(
        address indexed holder,
        uint256 indexed tokenId,
        uint256 indexed buyOrderNumber,
        uint256 timestamp
    );

    constructor(address _curve) ERC721("SINC Genesis Holder", "SINC-GEN") {
        curve = _curve;
    }

    function mint(address to, uint256 buyOrderNumber) external returns (uint256 tokenId) {
        require(msg.sender == curve, "Only curve");
        tokenId = nextTokenId++;
        _safeMint(to, tokenId);
        emit GenesisMinted(to, tokenId, buyOrderNumber, block.timestamp);
    }

    function _update(address to, uint256 tokenId, address auth)
        internal override returns (address)
    {
        address from = _ownerOf(tokenId);
        require(from == address(0) || to == address(0), "Soulbound: non-transferable");
        return super._update(to, tokenId, auth);
    }
}
