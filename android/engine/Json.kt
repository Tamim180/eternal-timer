package com.eternaltimer.engine

typealias JObj = MutableMap<String, Any?>
typealias JArr = MutableList<Any?>

/** Tiny dependency-free JSON (so the sync engine runs on a plain JVM for tests, and on Android). */
object Json {
    fun parse(text: String): Any? {
        val p = Parser(text)
        val v = p.readValue()
        p.skipWs()
        if (p.pos != text.length) throw IllegalArgumentException("Trailing characters")
        return v
    }

    @Suppress("UNCHECKED_CAST")
    fun parseObject(text: String): JObj? = try { parse(text) as? JObj } catch (e: Exception) { null }

    fun stringify(v: Any?): String {
        val sb = StringBuilder()
        write(sb, v)
        return sb.toString()
    }

    @Suppress("UNCHECKED_CAST")
    fun <T> deepCopy(v: T): T = parse(stringify(v)) as T

    private fun write(sb: StringBuilder, v: Any?) {
        when (v) {
            null -> sb.append("null")
            is Boolean -> sb.append(v.toString())
            is Int, is Long -> sb.append(v.toString())
            is Number -> {
                val d = v.toDouble()
                if (d.isNaN() || d.isInfinite()) sb.append("null") else sb.append(d.toString())
            }
            is String -> writeString(sb, v)
            is Map<*, *> -> {
                sb.append('{')
                var first = true
                for ((k, x) in v) {
                    if (!first) sb.append(',')
                    first = false
                    writeString(sb, k.toString())
                    sb.append(':')
                    write(sb, x)
                }
                sb.append('}')
            }
            is List<*> -> {
                sb.append('[')
                var first = true
                for (x in v) {
                    if (!first) sb.append(',')
                    first = false
                    write(sb, x)
                }
                sb.append(']')
            }
            else -> writeString(sb, v.toString())
        }
    }

    private fun writeString(sb: StringBuilder, s: String) {
        sb.append('"')
        for (c in s) {
            when {
                c == '"' -> sb.append("\\\"")
                c == '\\' -> sb.append("\\\\")
                c == '\n' -> sb.append("\\n")
                c == '\r' -> sb.append("\\r")
                c == '\t' -> sb.append("\\t")
                c < ' ' -> sb.append("\\u%04x".format(c.code))
                else -> sb.append(c)
            }
        }
        sb.append('"')
    }

    private class Parser(val s: String) {
        var pos = 0

        fun skipWs() {
            while (pos < s.length && s[pos].isWhitespace()) pos++
        }

        fun readValue(): Any? {
            skipWs()
            if (pos >= s.length) throw IllegalArgumentException("Unexpected end")
            return when (val c = s[pos]) {
                '{' -> readObject()
                '[' -> readArray()
                '"' -> readString()
                't' -> { expect("true"); true }
                'f' -> { expect("false"); false }
                'n' -> { expect("null"); null }
                else -> if (c == '-' || c.isDigit()) readNumber() else throw IllegalArgumentException("Bad char '$c' at $pos")
            }
        }

        private fun expect(word: String) {
            if (!s.startsWith(word, pos)) throw IllegalArgumentException("Expected $word at $pos")
            pos += word.length
        }

        private fun readObject(): JObj {
            val m = LinkedHashMap<String, Any?>()
            pos++
            skipWs()
            if (s[pos] == '}') { pos++; return m }
            while (true) {
                skipWs()
                val k = readString()
                skipWs()
                if (s[pos] != ':') throw IllegalArgumentException("Expected ':' at $pos")
                pos++
                m[k] = readValue()
                skipWs()
                when (s[pos]) {
                    ',' -> pos++
                    '}' -> { pos++; return m }
                    else -> throw IllegalArgumentException("Expected ',' or '}' at $pos")
                }
            }
        }

        private fun readArray(): JArr {
            val l = ArrayList<Any?>()
            pos++
            skipWs()
            if (s[pos] == ']') { pos++; return l }
            while (true) {
                l.add(readValue())
                skipWs()
                when (s[pos]) {
                    ',' -> pos++
                    ']' -> { pos++; return l }
                    else -> throw IllegalArgumentException("Expected ',' or ']' at $pos")
                }
            }
        }

        private fun readString(): String {
            if (s[pos] != '"') throw IllegalArgumentException("Expected string at $pos")
            pos++
            val sb = StringBuilder()
            while (true) {
                val c = s[pos++]
                when (c) {
                    '"' -> return sb.toString()
                    '\\' -> {
                        when (val e = s[pos++]) {
                            '"' -> sb.append('"')
                            '\\' -> sb.append('\\')
                            '/' -> sb.append('/')
                            'b' -> sb.append('\b')
                            'f' -> sb.append('\u000c')
                            'n' -> sb.append('\n')
                            'r' -> sb.append('\r')
                            't' -> sb.append('\t')
                            'u' -> { sb.append(s.substring(pos, pos + 4).toInt(16).toChar()); pos += 4 }
                            else -> throw IllegalArgumentException("Bad escape \\$e")
                        }
                    }
                    else -> sb.append(c)
                }
            }
        }

        private fun readNumber(): Any {
            val start = pos
            if (s[pos] == '-') pos++
            while (pos < s.length && (s[pos].isDigit() || s[pos] in ".eE+-")) pos++
            val t = s.substring(start, pos)
            return if (t.any { it == '.' || it == 'e' || it == 'E' }) t.toDouble() else (t.toLongOrNull() ?: t.toDouble())
        }
    }
}

fun JObj.str(k: String): String? = this[k] as? String
fun JObj.dbl(k: String): Double? = (this[k] as? Number)?.toDouble()
fun JObj.lng(k: String): Long = (this[k] as? Number)?.toLong() ?: 0L
fun JObj.bool(k: String): Boolean = this[k] as? Boolean ?: false

@Suppress("UNCHECKED_CAST")
fun JObj.arr(k: String): JArr {
    val v = this[k]
    if (v is MutableList<*>) return v as JArr
    val n: JArr = ArrayList()
    this[k] = n
    return n
}

@Suppress("UNCHECKED_CAST")
fun JObj.obj(k: String): JObj {
    val v = this[k]
    if (v is MutableMap<*, *>) return v as JObj
    val n: JObj = LinkedHashMap()
    this[k] = n
    return n
}
