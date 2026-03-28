// Package xcrypto provides X25519 key operations for Xray REALITY configs.
package xcrypto

import (
	"crypto/rand"
	"encoding/base64"
	"encoding/hex"
	"fmt"
	"os/exec"
	"strings"

	"golang.org/x/crypto/curve25519"
)

// DerivePublicKey computes the X25519 public key from a base64url-encoded private key.
// It first tries the `xray x25519 -i <priv>` command, then falls back to pure Go.
func DerivePublicKey(privateKeyB64 string) (string, error) {
	// Try xray binary first
	if pub, err := xrayDerivePublic(privateKeyB64); err == nil {
		return pub, nil
	}
	// Pure Go fallback
	return goDerivePublic(privateKeyB64)
}

func xrayDerivePublic(privB64 string) (string, error) {
	out, err := exec.Command("xray", "x25519", "-i", privB64).Output()
	if err != nil {
		return "", err
	}
	for _, line := range strings.Split(string(out), "\n") {
		line = strings.TrimSpace(line)
		if strings.HasPrefix(line, "Public key:") {
			return strings.TrimSpace(strings.TrimPrefix(line, "Public key:")), nil
		}
	}
	return "", fmt.Errorf("xray x25519: could not parse output")
}

func goDerivePublic(privB64 string) (string, error) {
	priv, err := base64.RawURLEncoding.DecodeString(privB64)
	if err != nil {
		// Try standard base64
		priv, err = base64.StdEncoding.DecodeString(privB64)
		if err != nil {
			return "", fmt.Errorf("decode private key: %w", err)
		}
	}
	if len(priv) != 32 {
		return "", fmt.Errorf("private key must be 32 bytes, got %d", len(priv))
	}
	// Clamp per RFC 7748
	priv[0] &= 248
	priv[31] &= 127
	priv[31] |= 64

	pub, err := curve25519.X25519(priv, curve25519.Basepoint)
	if err != nil {
		return "", fmt.Errorf("X25519: %w", err)
	}
	return base64.RawURLEncoding.EncodeToString(pub), nil
}

// GenKeypair generates a new X25519 key pair, returned as base64url strings.
func GenKeypair() (privateB64, publicB64 string, err error) {
	var priv [32]byte
	if _, err = rand.Read(priv[:]); err != nil {
		return
	}
	priv[0] &= 248
	priv[31] &= 127
	priv[31] |= 64

	pub, err := curve25519.X25519(priv[:], curve25519.Basepoint)
	if err != nil {
		return
	}
	privateB64 = base64.RawURLEncoding.EncodeToString(priv[:])
	publicB64 = base64.RawURLEncoding.EncodeToString(pub)
	return
}

// GenUUID generates a UUID v4 string.
func GenUUID() string {
	var b [16]byte
	rand.Read(b[:])
	b[6] = (b[6] & 0x0f) | 0x40
	b[8] = (b[8] & 0x3f) | 0x80
	return fmt.Sprintf("%08x-%04x-%04x-%04x-%012x",
		b[0:4], b[4:6], b[6:8], b[8:10], b[10:16])
}

// GenShortID generates a random hex string of the given byte length.
func GenShortID(byteLen int) string {
	b := make([]byte, byteLen)
	rand.Read(b)
	return hex.EncodeToString(b)
}
