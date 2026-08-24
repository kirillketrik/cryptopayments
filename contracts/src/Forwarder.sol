// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {SafeERC20} from "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";
import {ReentrancyGuard} from "@openzeppelin/contracts/utils/ReentrancyGuard.sol";

/// @notice Deposit-address logic shared by every EIP-1167 clone deployed by
/// {ForwarderFactory}. `FOUNDER_WALLET` is fixed at implementation-deploy
/// time and is never stored in per-clone state, so no admin call on any
/// clone can ever redirect where swept funds go. `sweep` is intentionally
/// unrestricted: anyone may trigger a forward, so funds can never be
/// stranded behind a lost or compromised operator key.
contract Forwarder is ReentrancyGuard {
    using SafeERC20 for IERC20;

    address public immutable FOUNDER_WALLET;

    /// @notice Hard ceiling on the native-currency fee a caller may claim in
    /// a single `sweep`, covering the blockchain fee they paid to call it.
    /// Immutable, same as `FOUNDER_WALLET` — bounding it at deploy time
    /// means a `sweep` call can never divert more than this fixed amount
    /// away from the founder wallet, however the actual `feeAmount` is set.
    /// If real gas costs outgrow this cap, deploy a new factory with a new
    /// cap rather than adding a settable one here.
    uint256 public immutable MAX_SWEEP_FEE;

    event Swept(
        address indexed founderWallet,
        uint256 nativeAmountToFounder,
        address[] tokens,
        uint256[] tokenAmounts,
        address feeRecipient,
        uint256 feeAmount
    );

    constructor(address founderWallet, uint256 maxSweepFee) {
        require(founderWallet != address(0), "Forwarder: zero founder wallet");
        FOUNDER_WALLET = founderWallet;
        MAX_SWEEP_FEE = maxSweepFee;
    }

    /// @notice Forward this contract's entire native balance, minus
    /// `feeAmount`, plus the entire balance of each address in `tokens`, to
    /// {FOUNDER_WALLET}. `feeAmount` (native currency only, capped by
    /// {MAX_SWEEP_FEE} and by the contract's actual native balance) is paid
    /// to `feeRecipient` — the blockchain fee for this call is meant to be
    /// covered out of the funds being swept, not by the caller eating it.
    /// Safe to call repeatedly and by anyone — each call only ever moves
    /// whatever balance is currently held, so a duplicate or late call is a
    /// harmless no-op (feeAmount = 0) rather than a double-spend.
    function sweep(address[] calldata tokens, uint256 feeAmount, address feeRecipient) external nonReentrant {
        uint256 nativeBalance = address(this).balance;
        require(feeAmount <= MAX_SWEEP_FEE, "Forwarder: fee exceeds max");
        require(feeAmount <= nativeBalance, "Forwarder: insufficient balance for fee");
        require(feeAmount == 0 || feeRecipient != address(0), "Forwarder: zero fee recipient");

        if (feeAmount > 0) {
            (bool feeOk,) = feeRecipient.call{value: feeAmount}("");
            require(feeOk, "Forwarder: fee transfer failed");
        }

        uint256 nativeToFounder = nativeBalance - feeAmount;
        if (nativeToFounder > 0) {
            (bool ok,) = FOUNDER_WALLET.call{value: nativeToFounder}("");
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

        emit Swept(FOUNDER_WALLET, nativeToFounder, tokens, tokenAmounts, feeRecipient, feeAmount);
    }

    /// @dev Accepts plain-value transfers with no calldata, e.g. a payer
    /// sending native currency directly to this deposit address.
    receive() external payable {}
}
