/* spra.c - streaming decoder of SPRA files (versions 0x01 and 0x02), the format of Theo H.
   (github.com/Tholsh/SPRA, docs/reduced_binary.md). Written from that description, not from its code.

   usage:  spra [--limit L] [--info] < FILE.spra > word.txt
     stdin   the raw SPRA bytes (a .spra.xz is decompressed by the caller)
     --info  print "n LENGTH TUPLES BITS ZERO_BASED VERSION" and stop
     --limit stop after L letters (exit 0; a test of the plumbing)
   The letters are the first n of 123456789ABC... (or 0123456789AB... when the zero-based flag is set), with no newline.
   Exit 0: decoded; 1: malformed file (reason on stderr; discard what was written); 2: write error.

   The word: the seed (first min(L, n-1) symbols), then for each logical tuple (depth d, offset o) d clean steps (each
   the one symbol missing from the previous n-1, which must be distinct) and one dirty letter copied from distance
   n - o, whose window of n must contain a repeat; after the last tuple, clean steps up to the length L. In version 2 a
   token (r, 0) with 1 <= r < 2^B - 1 repeats the last ordinary tuple r more times; (2^B - 1, 0) is reserved. Tokens are
   2B bits, MSB first; a half byte left at the end must be zero; nothing may follow. Every rule is checked here: a file
   that breaks one is refused rather than decoded differently. */
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#ifdef _WIN32
#include <fcntl.h>
#include <io.h>
#endif

static const char LAB1[] = "123456789ABCDEFGHIJKLMNOPQRSTUVW", LAB0[] = "0123456789ABCDEFGHIJKLMNOPQRSTUV";

static int n;
static uint64_t length, tuples, done, pos, limit = UINT64_MAX;
static const char *lab;

static void die(const char *m) {
    fprintf(stderr, "spra: %s (at letter %llu, tuple %llu)\n", m, (unsigned long long)pos, (unsigned long long)done);
    exit(1);
}

/* ---- input */
static unsigned char ibuf[1 << 16];
static size_t ilen, ipos;
static int getb(void) {
    if (ipos == ilen) {
        ilen = fread(ibuf, 1, sizeof ibuf, stdin);
        ipos = 0;
        if (ilen == 0) return -1;
    }
    return ibuf[ipos++];
}
static int have_half;          /* a low half byte is waiting */
static unsigned half;
static int nibble(void) {      /* next 4 bits, MSB first; -1 at the end */
    if (have_half) { have_half = 0; return (int)half; }
    int b = getb();
    if (b < 0) return -1;
    half = (unsigned)b & 15u;
    have_half = 1;
    return b >> 4;
}

/* ---- output */
static unsigned char obuf[1 << 20];
static size_t olen;
static void flush_out(void) {
    if (olen && fwrite(obuf, 1, olen, stdout) != olen) exit(2);
    olen = 0;
}

/* ---- word state: the last symbols in a ring, counts of the symbols among the last n-1 */
#define RING 128               /* > the largest copy distance, n + 32 <= 64 */
static unsigned char ring[RING];
static int cnt[32], present;   /* present = number of symbols with cnt > 0 */

static void put(int s) {
    if (pos >= length) die("more letters than the header length");
    if (pos >= limit) { flush_out(); exit(0); }
    ring[pos % RING] = (unsigned char)s;
    obuf[olen++] = (unsigned char)lab[s];
    if (olen == sizeof obuf) flush_out();
    if (cnt[s]++ == 0) present++;
    pos++;
    if (pos > (uint64_t)(n - 1)) {             /* the symbol n back leaves the window of the last n-1 */
        int r = ring[(pos - (uint64_t)n) % RING];
        if (--cnt[r] == 0) present--;
    }
}

static void clean_step(void) {
    if (pos < (uint64_t)(n - 1) || present != n - 1) die("a clean step after n-1 letters that are not distinct");
    int s = 0;
    while (cnt[s]) s++;                        /* the one symbol missing from the last n-1 */
    put(s);
}

static void tuple(unsigned d, int o) {
    for (unsigned i = 0; i < d; i++) clean_step();
    int dist = n - o;
    if (dist < 1 || (uint64_t)dist > pos || dist >= RING) die("copy distance outside the word");
    int s = ring[(pos - (uint64_t)dist) % RING];
    if (pos < (uint64_t)(n - 1)) die("a dirty letter inside the seed");
    if (present == n - 1 && cnt[s] == 0) die("the copied letter makes a clean window (a dirty tuple must not)");
    put(s);
    done++;
}

static uint64_t le64(const unsigned char *p) {
    uint64_t v = 0;
    for (int i = 7; i >= 0; i--) v = (v << 8) | p[i];
    return v;
}

int main(int argc, char **argv) {
    int info = 0;
    for (int i = 1; i < argc; i++) {
        if (!strcmp(argv[i], "--info")) info = 1;
        else if (!strcmp(argv[i], "--limit") && i + 1 < argc) limit = strtoull(argv[++i], NULL, 10);
        else { fprintf(stderr, "usage: spra [--limit L] [--info] < FILE.spra\n"); return 2; }
    }
#ifdef _WIN32
    _setmode(_fileno(stdin), _O_BINARY);
    _setmode(_fileno(stdout), _O_BINARY);
#endif
    unsigned char h[24];
    for (int i = 0; i < 24; i++) {
        int b = getb();
        if (b < 0) die("file shorter than the 24-byte header");
        h[i] = (unsigned char)b;
    }
    if (memcmp(h, "SPRA", 4)) die("not a SPRA file (magic)");
    int ver = h[4], layout = h[5], flags = h[6];
    if (ver != 1 && ver != 2) die("unknown version");
    if (layout != 0x44 && layout != 0x66) die("unknown layout");
    if (flags & ~1) die("reserved flag bits set");
    n = h[7];
    if (n < 1 || n > 32) die("n outside 1..32");
    int B = layout == 0x44 ? 4 : 6;
    length = le64(h + 8);
    tuples = le64(h + 16);
    lab = (flags & 1) ? LAB0 : LAB1;
    if (info) {
        printf("%d %llu %llu %d %d %d\n", n, (unsigned long long)length, (unsigned long long)tuples, B, flags & 1, ver);
        return 0;
    }
    uint64_t seed = length < (uint64_t)(n - 1) ? length : (uint64_t)(n - 1);
    if (tuples > 0 && length < (uint64_t)n) die("tuples in a word shorter than n");
    if (tuples > length) die("more tuples than letters");
    for (uint64_t i = 0; i < seed; i++) {
        int b = getb();
        if (b < 0) die("file ends inside the seed");
        if (b >= n) die("seed symbol outside 0..n-1");
        put(b);
    }
    const unsigned M = (1u << B) - 1, nib = (unsigned)(2 * B / 4);
    int have_last = 0;
    unsigned last_d = 0;
    int last_o = 0;
    while (done < tuples) {
        unsigned t = 0;
        for (unsigned k = 0; k < nib; k++) {
            int x = nibble();
            if (x < 0) die("file ends before the stated number of tuples");
            t = (t << 4) | (unsigned)x;
        }
        unsigned d = t >> B, of = t & M;
        int o = of >= (1u << (B - 1)) ? (int)of - (int)(1u << B) : (int)of;
        if (ver == 2 && d >= 1 && of == 0) {   /* repeat marker */
            if (d == M) die("reserved extension token");
            if (!have_last) die("a repeat marker before any tuple");
            if (d > tuples - done) die("a repeat marker beyond the stated number of tuples");
            for (unsigned r = 0; r < d; r++) tuple(last_d, last_o);
        } else {
            tuple(d, o);
            have_last = 1, last_d = d, last_o = o;
        }
    }
    if (have_half && half != 0) die("padding bits are not zero");
    if (B == 4 && have_half) die("a half token at the end");
    if (getb() >= 0) die("extra bytes after the last token");
    if (n == 1) {                              /* the one symbol repeated; no tuples are possible */
        while (pos < length) put(0);
    } else {
        while (pos < length) clean_step();
    }
    flush_out();
    return 0;
}
