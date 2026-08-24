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

    /// @notice Forward this contract's entire native balance, minus the
    /// actual fee paid, plus the entire balance of each address in
    /// `tokens`, to {FOUNDER_WALLET}. The caller requests `feeAmount`
    /// (native currency only) to cover the blockchain fee they paid to call
    /// this — the actual amount paid to `feeRecipient` is silently clamped
    /// down to `min(feeAmount, MAX_SWEEP_FEE, current native balance)`
    /// rather than reverting the whole sweep, so a stale or over-optimistic
    /// fee request (or a race between two callers sweeping the same clone)
    /// never blocks the forward-to-founder itself — worst case the caller
    /// is paid less than they asked for. The actual amount paid is always
    /// visible in the `Swept` event regardless of what was requested.
    /// Safe to call repeatedly and by anyone — each call only ever moves
    /// whatever balance is currently held, so a duplicate or late call is a
    /// harmless no-op rather than a double-spend.
    function sweep(address[] calldata tokens, uint256 feeAmount, address feeRecipient) external nonReentrant {
        uint256 nativeBalance = address(this).balance;

        uint256 feePaid = feeAmount;
        if (feePaid > MAX_SWEEP_FEE) feePaid = MAX_SWEEP_FEE;
        if (feePaid > nativeBalance) feePaid = nativeBalance;
        require(feePaid == 0 || feeRecipient != address(0), "Forwarder: zero fee recipient");

        if (feePaid > 0) {
            (bool feeOk,) = feeRecipient.call{value: feePaid}("");
            require(feeOk, "Forwarder: fee transfer failed");
        }

        uint256 nativeToFounder = nativeBalance - feePaid;
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

        emit Swept(FOUNDER_WALLET, nativeToFounder, tokens, tokenAmounts, feeRecipient, feePaid);
    }

    /// @dev Accepts plain-value transfers with no calldata, e.g. a payer
    /// sending native currency directly to this deposit address.
    receive() external payable {}
}
