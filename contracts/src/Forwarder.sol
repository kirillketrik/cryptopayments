// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {SafeERC20} from "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";

/// @notice Deposit-address logic shared by every EIP-1167 clone deployed by
/// {ForwarderFactory}. `FOUNDER_WALLET` is fixed at implementation-deploy
/// time and is never stored in per-clone state, so no admin call on any
/// clone can ever redirect where swept funds go. `sweep` is intentionally
/// unrestricted: anyone may trigger a forward, so funds can never be
/// stranded behind a lost or compromised operator key.
contract Forwarder {
    using SafeERC20 for IERC20;

    address public immutable FOUNDER_WALLET;

    event Swept(address indexed founderWallet, uint256 nativeAmount, address[] tokens, uint256[] tokenAmounts);

    constructor(address founderWallet) {
        require(founderWallet != address(0), "Forwarder: zero founder wallet");
        FOUNDER_WALLET = founderWallet;
    }

    /// @notice Forward this contract's entire native balance, plus the
    /// entire balance of each address in `tokens`, to {FOUNDER_WALLET}.
    /// Safe to call repeatedly and by anyone — each call only ever moves
    /// whatever balance is currently held, so a duplicate or late call is a
    /// harmless no-op rather than a double-spend.
    function sweep(address[] calldata tokens) external {
        uint256 nativeAmount = address(this).balance;
        if (nativeAmount > 0) {
            (bool ok,) = FOUNDER_WALLET.call{value: nativeAmount}("");
            require(ok, "Forwarder: native transfer failed");
        }

        uint256[] memory tokenAmounts = new uint256[](tokens.length);
        for (uint256 i = 0; i < tokens.length; i++) {
            IERC20 token = IERC20(tokens[i]);
            uint256 amount = token.balanceOf(address(this));
            tokenAmounts[i] = amount;
            if (amount > 0) {
                token.safeTransfer(FOUNDER_WALLET, amount);
            }
        }

        emit Swept(FOUNDER_WALLET, nativeAmount, tokens, tokenAmounts);
    }

    /// @dev Accepts plain-value transfers with no calldata, e.g. a payer
    /// sending native currency directly to this deposit address.
    receive() external payable {}
}
