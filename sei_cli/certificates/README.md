# Supplemental public TLS chain

These public, cross-signed CA certificates complete the chain observed at
`sei.rn.gov.br` on 2026-10-03. They add no self-signed root or leaf pin.
The verifier must keep hostname/validity checks and disable partial-chain
validation, requiring a path to a root already trusted by certifi.

Official source: https://letsencrypt.org/certificates/

| Certificate | Official PEM | Valid until | SHA-256 |
|---|---|---|---|
| YE1, signed by Root YE | https://letsencrypt.org/certs/gen-y/int-ye1.pem | 2028-09-02 | A2372D06431E9716365EEED47EC020351497D182FCC038E457E58168A03CAC07 |
| Root YE, cross-signed by ISRG Root X2 | https://letsencrypt.org/certs/gen-y/root-ye-by-x2.pem | 2032-09-02 | 0FC0901CCA2BAE9E9FDBB02D50D02F1094F7B36672086991B9E897626DC485F0 |
| ISRG Root X2, cross-signed by ISRG Root X1 | https://letsencrypt.org/certs/gen-y/root-x2-by-x1.pem | 2032-09-02 | EE5F7ABD6981BB0255632CD8F49283451B4B18844D12040B44EE00F07B8FE2C6 |

No download occurs during client creation. Invalid or expired chains must fail
validation. Review official replacement intermediates if the server changes
issuer or bundled certificates expire; a valid complete chain remains accepted.
Do not replace this with an unverified certificate or disable TLS validation.
