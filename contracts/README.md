# contracts (EVM proxy-forwarder)

Foundry project implementing the EVM leg of the "strict to founder wallet" proxy
strategy described in [../docs/proxy-contract-strategy.md](../docs/proxy-contract-strategy.md).
This is the first of the four target chains (EVM, Tron, TON, Solana) — it's the reference
implementation the others are meant to follow.

## What's here

- `src/Forwarder.sol` — the shared implementation contract. Every deposit address is an
  EIP-1167 minimal-proxy clone of this contract. `FOUNDER_WALLET` is immutable, baked in
  at implementation-deploy time, and shared by every clone via `delegatecall` — no clone
  has its own settable destination. `sweep(tokens)` forwards the clone's native balance
  plus the balance of each address in `tokens` to `FOUNDER_WALLET`, and is callable by
  anyone.
- `src/ForwarderFactory.sol` — deploys the `Forwarder` implementation once (constructor
  arg: the founder wallet address) and from then on exposes `computeAddress(salt)` (pure
  off-chain-computable CREATE2 address prediction — this is what a real `AddressProvider`
  calls to hand out a deposit address without submitting a transaction) and
  `deployAndSweep(salt, tokens)` (deploys the clone on first use, then sweeps — this is
  what a real `SweepStrategy` calls).
- `test/ForwarderFactory.t.sol` — covers deterministic address prediction, receiving
  funds before deployment, native + ERC-20 sweeping, idempotent/repeat sweeps, permissionless
  calling (fuzzed caller), and that every clone shares the same immutable founder wallet.
- `script/DeployForwarderFactory.s.sol` — one-time factory deployment script. Reads
  `PRIVATE_KEY` and `FOUNDER_WALLET` from the environment.

## Commands

```shell
forge build
forge test -vv
forge fmt
```

Deploy a factory (requires `PRIVATE_KEY` and `FOUNDER_WALLET` env vars):

```shell
forge script script/DeployForwarderFactory.s.sol --rpc-url <network> --broadcast --verify
```

## Dependencies

- `forge-std` — testing/scripting utilities.
- `openzeppelin-contracts` — `Clones` (EIP-1167 minimal-proxy CREATE2 deployment) and
  `IERC20`/`SafeERC20` for token sweeping.

Both are Foundry git submodules under `lib/`; `forge install` after cloning if they're
missing.

## Not yet implemented

Real `AddressProvider`/`SweepStrategy` Python implementations that call into this
factory (see the mapping table in the design doc), relayer key management for who pays
gas for `deployAndSweep`, and mainnet/testnet deployment addresses.
