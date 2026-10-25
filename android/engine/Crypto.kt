package com.eternaltimer.engine

import java.security.SecureRandom
import javax.crypto.Cipher
import javax.crypto.Mac
import javax.crypto.spec.GCMParameterSpec
import javax.crypto.spec.SecretKeySpec

/** Pairing code -> topic + key, and AES-256-GCM. Byte-for-byte compatible with eternal_timer_sync.py. */
object Crypto {
    private const val B32 = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567"
    private val AAD = "eternal-timer-v1".toByteArray()

    fun newSecret(): String {
        val raw = ByteArray(20)
        SecureRandom().nextBytes(raw)
        return formatSecret(raw)
    }

    fun formatSecret(raw: ByteArray): String = base32(raw).chunked(4).joinToString("-")

    fun parseSecret(code: String): ByteArray {
        val c = code.uppercase().filter { it.isLetterOrDigit() }
        if (c.length != 32 || c.any { B32.indexOf(it) < 0 }) {
            throw IllegalArgumentException("That doesn't look like a valid pairing code")
        }
        val out = ByteArray(20)
        var acc = 0
        var bits = 0
        var n = 0
        for (ch in c) {
            acc = (acc shl 5) or B32.indexOf(ch)
            bits += 5
            if (bits >= 8) {
                bits -= 8
                out[n++] = ((acc shr bits) and 0xFF).toByte()
            }
        }
        return out
    }

    private fun base32(raw: ByteArray): String {
        val sb = StringBuilder()
        var acc = 0
        var bits = 0
        for (b in raw) {
            acc = (acc shl 8) or (b.toInt() and 0xFF)
            bits += 8
            while (bits >= 5) {
                bits -= 5
                sb.append(B32[(acc shr bits) and 31])
            }
        }
        if (bits > 0) sb.append(B32[(acc shl (5 - bits)) and 31])
        return sb.toString()
    }

    private fun hmac(key: ByteArray, msg: String): ByteArray {
        val mac = Mac.getInstance("HmacSHA256")
        mac.init(SecretKeySpec(key, "HmacSHA256"))
        return mac.doFinal(msg.toByteArray())
    }

    fun hex(b: ByteArray): String = b.joinToString("") { "%02x".format(it) }

    /** -> (topic, key) */
    fun derive(secret: ByteArray): Pair<String, ByteArray> {
        val topic = "etimer-" + hex(hmac(secret, "eternal-timer/topic/v1")).substring(0, 40)
        return topic to hmac(secret, "eternal-timer/key/v1")
    }

    fun encrypt(key: ByteArray, data: ByteArray, nonce: ByteArray? = null): String {
        val n = nonce ?: ByteArray(12).also { SecureRandom().nextBytes(it) }
        val c = Cipher.getInstance("AES/GCM/NoPadding")
        c.init(Cipher.ENCRYPT_MODE, SecretKeySpec(key, "AES"), GCMParameterSpec(128, n))
        c.updateAAD(AAD)
        val ct = c.doFinal(data)
        return java.util.Base64.getUrlEncoder().withoutPadding().encodeToString(n + ct)
    }

    fun decrypt(key: ByteArray, text: String): ByteArray {
        val raw = java.util.Base64.getUrlDecoder().decode(text.trim())
        val c = Cipher.getInstance("AES/GCM/NoPadding")
        c.init(Cipher.DECRYPT_MODE, SecretKeySpec(key, "AES"), GCMParameterSpec(128, raw.copyOfRange(0, 12)))
        c.updateAAD(AAD)
        return c.doFinal(raw.copyOfRange(12, raw.size))
    }
}
