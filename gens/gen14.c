/* gen14.c - streaming generator for superpermutations on 14 symbols built from a selection on 11 symbols
   (K = 10 letters, rows F / S / D) by two transports and the completion.  The construction (slices, transport,
   completion, closed trails) is Jay Pantone's; the order of the 2-lift that William Echols published on 2026-10-17
   (recipe "superperm-2lift/1") is reproduced in mode `echols`; this code is written from our own definitions
   (opt/pieces/k10/gen12.py, publish/superpermutations/arrange/gen13.py), not copied.

   usage:
     gen14 info   SEL                              counts and the ledger of the selection
     gen14 echols SEL [--limit L] [--alpha A]      Echols's order; word to stdout (alphabet 123456789ABCDE)
     gen14 plan   SEL PLAN [--alpha A] [--events EV] [--limit L] [--count]
                                                   a unit plan (see below); word to stdout (alphabet 0..9ABCD)
     --count : do not write the word, only compute length / SHA-256 (still spells every letter)
     --nosha : skip SHA-256

   Letters at n = 14: 0..9 the letters of SEL, 10 and 11 the two transported letters, 12 the distinguished
   letter, 13 the completion letter.  h = 11.

   Units (one per closed trail at n = 13; 72,000 for selection C):
     D d g j    small unit: detached loop d (index among the D rows of SEL, file order), y = x_d with letter 10
                inserted before x_d[g] (g = 0..9); the 11 small trails "11 inserted in y at position a" for
                a = j, j+1, .., j+10 (mod 11), each written as a closed piece (R + h letters) from its vertex
                rot(y, a); first word rot(y, j), last word rot(y, j - 1).
     W w i0 way walk unit: walk w at 12 letters (the 2-lift walks in Echols's order: walks of SEL in file order,
                transport orbits by first port, twice), cut at its full row b = row i0; way 0: completion letter
                in the gaps 0..10 of b (first word b[0..10], last word b[11] b[0..9]); way 1: gaps 1..11 (first
                word b[1..11], last word b[0..10]).  11 trails, each cut at a weight-2 step, joins of cost 0.
   Consecutive units overlap in the largest overlap (<= h letters) of the last h letters written and the first
   h letters of the next unit; an optional 5th field "oK" caps that overlap at K.
   PLAN header line: "N14-UNITPLAN 1"; lines starting with '#' are ignored.

   Events file (--events): one line per closed trail written: index, word offset of its piece, piece length,
   overlap with the word before, R, kind (D/W), unit fields, trail-in-unit index, opening offset in the cyclic
   word as generated here.

   Prints to stderr: length, SHA-256 of the word as written (with the chosen alphabet, no newline). */
#define _FILE_OFFSET_BITS 64
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include <time.h>
#ifdef _WIN32
#include <fcntl.h>
#include <io.h>
#endif
typedef uint64_t u64; typedef uint32_t u32; typedef uint8_t u8; typedef long long i64;
#define DIE(...) do { fprintf(stderr, "gen14: " __VA_ARGS__); fprintf(stderr, "\n"); exit(1); } while (0)
#define NN 14
#define HH 11
static double wall(void) { struct timespec ts; timespec_get(&ts, TIME_UTC); return ts.tv_sec + ts.tv_nsec * 1e-9; }

/* ------------------------------------------------------------------ SHA-256 */
typedef struct { u32 h[8]; u8 buf[64]; u64 len; int n; } Sha;
static const u32 SK[64] = {
0x428a2f98,0x71374491,0xb5c0fbcf,0xe9b5dba5,0x3956c25b,0x59f111f1,0x923f82a4,0xab1c5ed5,0xd807aa98,0x12835b01,0x243185be,0x550c7dc3,
0x72be5d74,0x80deb1fe,0x9bdc06a7,0xc19bf174,0xe49b69c1,0xefbe4786,0x0fc19dc6,0x240ca1cc,0x2de92c6f,0x4a7484aa,0x5cb0a9dc,0x76f988da,
0x983e5152,0xa831c66d,0xb00327c8,0xbf597fc7,0xc6e00bf3,0xd5a79147,0x06ca6351,0x14292967,0x27b70a85,0x2e1b2138,0x4d2c6dfc,0x53380d13,
0x650a7354,0x766a0abb,0x81c2c92e,0x92722c85,0xa2bfe8a1,0xa81a664b,0xc24b8b70,0xc76c51a3,0xd192e819,0xd6990624,0xf40e3585,0x106aa070,
0x19a4c116,0x1e376c08,0x2748774c,0x34b0bcb5,0x391c0cb3,0x4ed8aa4a,0x5b9cca4f,0x682e6ff3,0x748f82ee,0x78a5636f,0x84c87814,0x8cc70208,
0x90befffa,0xa4506ceb,0xbef9a3f7,0xc67178f2};
#define ROR(x,n) (((x)>>(n))|((x)<<(32-(n))))
static void sha_block(u32 *h, const u8 *p) {
    u32 w[64];
    for (int i = 0; i < 16; i++) w[i] = (u32)p[4*i]<<24 | (u32)p[4*i+1]<<16 | (u32)p[4*i+2]<<8 | p[4*i+3];
    for (int i = 16; i < 64; i++) { u32 s0 = ROR(w[i-15],7)^ROR(w[i-15],18)^(w[i-15]>>3), s1 = ROR(w[i-2],17)^ROR(w[i-2],19)^(w[i-2]>>10); w[i] = w[i-16]+s0+w[i-7]+s1; }
    u32 a=h[0],b=h[1],c=h[2],d=h[3],e=h[4],f=h[5],g=h[6],hh=h[7];
    for (int i = 0; i < 64; i++) {
        u32 t1 = hh + (ROR(e,6)^ROR(e,11)^ROR(e,25)) + ((e&f)^(~e&g)) + SK[i] + w[i];
        u32 t2 = (ROR(a,2)^ROR(a,13)^ROR(a,22)) + ((a&b)^(a&c)^(b&c));
        hh=g; g=f; f=e; e=d+t1; d=c; c=b; b=a; a=t1+t2;
    }
    h[0]+=a; h[1]+=b; h[2]+=c; h[3]+=d; h[4]+=e; h[5]+=f; h[6]+=g; h[7]+=hh;
}
static void sha_init(Sha *s) { static const u32 iv[8] = {0x6a09e667,0xbb67ae85,0x3c6ef372,0xa54ff53a,0x510e527f,0x9b05688c,0x1f83d9ab,0x5be0cd19}; memcpy(s->h, iv, 32); s->len = 0; s->n = 0; }
static void sha_update(Sha *s, const u8 *p, size_t L) {
    s->len += L;
    if (s->n) { while (L && s->n < 64) { s->buf[s->n++] = *p++; L--; } if (s->n == 64) { sha_block(s->h, s->buf); s->n = 0; } }
    while (L >= 64) { sha_block(s->h, p); p += 64; L -= 64; }
    while (L) { s->buf[s->n++] = *p++; L--; }
}
static void sha_final(Sha *s, char *hex) {
    u64 bits = s->len * 8; u8 pad = 0x80; sha_update(s, &pad, 1); pad = 0;
    while (s->n != 56) sha_update(s, &pad, 1);
    u8 lb[8]; for (int i = 0; i < 8; i++) lb[i] = (u8)(bits >> (56 - 8*i)); sha_update(s, lb, 8);
    for (int i = 0; i < 8; i++) sprintf(hex + 8*i, "%08x", s->h[i]);
}

/* ------------------------------------------------------------------ output */
#define OBUF (64u << 20)
static u8 *obuf; static size_t on = 0; static u64 written = 0, limit = ~0ULL;
static u8 alpha[16]; static Sha sha; static int dosha = 1, dowrite = 1, limited = 0;
static u8 tail[HH]; static int ntail = 0;          /* last h letters written (letter values) */
static void flush_out(void) {
    if (!on) return;
    for (size_t i = 0; i < on; i++) obuf[i] = alpha[obuf[i]];
    if (dosha) sha_update(&sha, obuf, on);
    if (dowrite && fwrite(obuf, 1, on, stdout) != on) DIE("write failed");
    on = 0;
}
static void emit(const u8 *p, size_t L) {
    if (limited) return;
    if (written + L >= limit) { L = (size_t)(limit - written); limited = 1; }
    while (L) {
        size_t c = OBUF - on; if (c > L) c = L;
        memcpy(obuf + on, p, c); on += c; written += c; p += c; L -= c;
        if (on == OBUF) flush_out();
    }
}

/* ------------------------------------------------------------------ selection and walks */
typedef struct { u8 x[12]; u8 L; u8 ty; u8 lp; } Row;   /* ty 0 = F, 1 = S; lp: made at the last port of a short row */
static Row *sel; static int nsel; static int *det; static int ndet;
static int *walkrow, *walkstart, nwalk;            /* walks of SEL in Echols's order (file order of first row) */

static u64 key8(const u8 *x) { u64 k = 0; for (int i = 0; i < 8; i++) k = k << 4 | x[i]; return k; }
static void rot(u8 *dst, const u8 *b, int L, int j) { j %= L; if (j < 0) j += L; for (int i = 0; i < L; i++) dst[i] = b[(i + j) % L]; }
static void ins(u8 *dst, const u8 *b, int L, int z, int j) { int q = 0; for (int i = 0; i < L; i++) { if (i == j) dst[q++] = (u8)z; dst[q++] = b[i]; } if (j == L) dst[q++] = (u8)z; }

static void read_sel(const char *path) {
    FILE *f = fopen(path, "r"); if (!f) DIE("cannot open %s", path);
    char line[256], xs[64], ts[64]; int cap = 400000;
    sel = malloc(sizeof(Row) * cap); det = malloc(sizeof(int) * cap); nsel = ndet = 0;
    int *isdet = malloc(sizeof(int) * cap);
    while (fgets(line, sizeof line, f)) {
        if (line[0] == '#' || line[0] == '\n' || line[0] == '\r') continue;
        if (sscanf(line, "%63s %63s", xs, ts) != 2) continue;
        if (strlen(xs) != 10) DIE("row length");
        Row r; r.L = 10; r.lp = 0;
        {
            int seen10[10] = {0};
            for (int i = 0; i < 10; i++) {
                char c = xs[i];
                if (c < '0' || c > '9') DIE("row has a character outside 0-9");
                int v = c - '0';
                if (seen10[v]++) DIE("row repeats a digit");
                r.x[i] = (u8)v;
            }
        }
        if (ts[0] == 'F') r.ty = 0; else if (ts[0] == 'S') r.ty = 1; else if (ts[0] == 'D') r.ty = 2; else DIE("type");
        if (nsel == cap) DIE("too many rows");
        if (r.ty == 2) det[ndet++] = nsel;
        sel[nsel++] = r;
    }
    fclose(f); free(isdet);
    /* heads: first 8 letters; tail of F: rot(b,1)[:8], of S: rot(b,-1)[:8] */
    u64 hm = 1; while (hm < (u64)nsel * 4) hm <<= 1;
    u64 *hk = malloc(sizeof(u64) * hm); int *hv = malloc(sizeof(int) * hm); memset(hk, 0xff, sizeof(u64) * hm);
    for (int i = 0; i < nsel; i++) if (sel[i].ty != 2) {
        u64 k = key8(sel[i].x), p = (k * 0x9E3779B97F4A7C15ULL) & (hm - 1);
        while (hk[p] != ~0ULL) { if (hk[p] == k) DIE("repeated head"); p = (p + 1) & (hm - 1); }
        hk[p] = k; hv[p] = i;
    }
    char *seen = calloc(nsel, 1); walkrow = malloc(sizeof(int) * nsel); walkstart = malloc(sizeof(int) * (nsel + 1)); nwalk = 0; int nw = 0;
    for (int i = 0; i < nsel; i++) {
        if (sel[i].ty == 2 || seen[i]) continue;
        walkstart[nwalk++] = nw; int j = i;
        while (!seen[j]) {
            seen[j] = 1; walkrow[nw++] = j;
            u8 t[10]; rot(t, sel[j].x, 10, sel[j].ty == 0 ? 1 : -1);
            u64 k = key8(t), p = (k * 0x9E3779B97F4A7C15ULL) & (hm - 1);
            while (hk[p] != k) { if (hk[p] == ~0ULL) DIE("unbalanced walk"); p = (p + 1) & (hm - 1); }
            j = hv[p];
        }
        if (j != i) DIE("walk does not close at its first row");
    }
    walkstart[nwalk] = nw;
    free(hk); free(hv); free(seen);
}

/* transport one walk (rows of length L, letters 0..L-1 used) by inserting letter L; appends the orbit walks */
typedef struct { Row *r; int n, cap; } RowVec;
static void rv_push(RowVec *v, const Row *r) { if (v->n == v->cap) { v->cap = v->cap ? v->cap * 2 : 1024; v->r = realloc(v->r, sizeof(Row) * v->cap); } v->r[v->n++] = *r; }
/* out: rows of all orbits concatenated, ostart[] the start of each orbit; returns number of orbits */
static int transport(const Row *w, int m, RowVec *out, int *ostart) {
    int L = w[0].L, H = L - 1, k = L; char done[16] = {0}; int no = 0;
    for (int p0 = 0; p0 < H; p0++) {
        if (done[p0]) continue;
        ostart[no++] = out->n; int p = p0, i = 0;
        for (;;) {
            if (i == 0) { if (done[p]) DIE("repeated transport port"); done[p] = 1; }
            const Row *r = &w[i]; Row a; a.L = (u8)(L + 1); a.ty = r->ty; a.lp = 0;
            if (r->ty == 0) {
                if (p == 0) { ins(a.x, r->x, L, k, 0); rv_push(out, &a); ins(a.x, r->x, L, k, L - 1); rv_push(out, &a); }
                else { ins(a.x, r->x, L, k, p); rv_push(out, &a); }
            } else {
                if (p < L - 2) { ins(a.x, r->x, L, k, p); rv_push(out, &a); }
                else { a.lp = 1; ins(a.x, r->x, L, k, L - 2); rv_push(out, &a); u8 y[16]; ins(y, r->x, L, k, L - 1); rot(a.x, y, L + 1, -1); rv_push(out, &a); }
            }
            p = (p + (r->ty == 0 ? -1 : 1) + H) % H;
            if (++i == m) { i = 0; if (p == p0) break; }
        }
    }
    ostart[no] = out->n;
    return no;
}

/* the 2-lift walks: W2 rows packed */
static u64 *w2row; static u8 *w2ty, *w2lp; static u32 *w2start; static int nw2;
static int *w2walk;                                  /* walk of SEL for each W2 */
static int *w2a, *w2b;                               /* first / second orbit index */
static void build_w2(void) {
    RowVec v1 = {0}, v2 = {0}; int os1[16], os2[16];
    u64 cap = 40000000ULL; w2row = malloc(8 * cap); w2ty = malloc(cap); w2lp = malloc(cap); w2start = malloc(4 * 20000);
    w2walk = malloc(4 * 20000); w2a = malloc(4 * 20000); w2b = malloc(4 * 20000);
    u64 nr = 0; nw2 = 0;
    Row *tmp = malloc(sizeof(Row) * nsel);
    for (int wi = 0; wi < nwalk; wi++) {
        int m = walkstart[wi + 1] - walkstart[wi];
        for (int i = 0; i < m; i++) tmp[i] = sel[walkrow[walkstart[wi] + i]];
        v1.n = 0; int n1 = transport(tmp, m, &v1, os1);
        for (int a = 0; a < n1; a++) {
            v2.n = 0; int n2 = transport(v1.r + os1[a], os1[a + 1] - os1[a], &v2, os2);
            for (int b = 0; b < n2; b++) {
                w2start[nw2] = (u32)nr; w2walk[nw2] = wi; w2a[nw2] = a; w2b[nw2] = b; nw2++;
                for (int q = os2[b]; q < os2[b + 1]; q++) {
                    u64 k = 0; for (int t = 0; t < 12; t++) k |= (u64)v2.r[q].x[t] << (4 * t);
                    if (nr == cap) DIE("too many W2 rows");
                    w2row[nr] = k; w2ty[nr] = v2.r[q].ty; w2lp[nr] = v2.r[q].lp; nr++;
                }
            }
        }
    }
    w2start[nw2] = (u32)nr;
    free(tmp); free(v1.r); free(v2.r);
}
static void unpack12(u64 k, u8 *x) { for (int t = 0; t < 12; t++) x[t] = (k >> (4 * t)) & 15; }

/* ------------------------------------------------------------------ spelling */
static u8 *tb; static size_t tcap = 0, tn = 0;       /* cyclic word of the current trail */
static void tput(const u8 *p, size_t L) { if (tn + L > tcap) { tcap = (tn + L) * 2 + 4096; tb = realloc(tb, tcap); if (!tb) DIE("oom"); } memcpy(tb + tn, p, L); tn += L; }
/* one slice: B (13 letters), satellite s, visible v; contribution = B s B (B[t] s rot(B,t+1))_{t<v-1} minus the last h */
static void slice(const u8 *B, int s, int v) {
    u8 w[16 + 15 * 14]; int q = 0;
    for (int i = 0; i < 13; i++) w[q++] = B[i];
    w[q++] = (u8)s;
    for (int i = 0; i < 13; i++) w[q++] = B[i];
    for (int t = 0; t < v - 1; t++) { w[q++] = B[t]; w[q++] = (u8)s; for (int i = 0; i < 13; i++) w[q++] = B[(t + 1 + i) % 13]; }
    tput(w, q - HH);
}
/* completion port path of a 12-letter row b with port p; records slice starts in st[] (returns count) */
static int portpath(const u8 *b, int ty, int p, size_t *st) {
    u8 B[16]; int ns = 0;
    if (ty == 0) {
        if (p == 0) { st[ns++] = tn; ins(B, b, 12, 13, 0); slice(B, 12, 13); st[ns++] = tn; ins(B, b, 12, 13, 11); slice(B, 12, 13); }
        else { st[ns++] = tn; ins(B, b, 12, 13, p); slice(B, 12, 13); }
    } else {
        if (p < 10) { st[ns++] = tn; ins(B, b, 12, 13, p); slice(B, 12, 11); }
        else {
            st[ns++] = tn; ins(B, b, 12, 13, 10); slice(B, 12, 10);
            st[ns++] = tn; rot(B, b, 12, 10); B[12] = 12; slice(B, 13, 13);
            st[ns++] = tn; rot(B, b, 12, 11); B[12] = 12; slice(B, 13, 13);
            st[ns++] = tn; ins(B, b, 12, 13, 11); slice(B, 12, 10);
        }
    }
    return ns;
}
/* closed trail of W2 walk w passing row i0 with port p, spelled from row i0; st0[] = slice starts at row i0 */
static int nst0; static size_t st0[4];
static void walk_trail(int w, int i0, int p) {
    u32 a = w2start[w]; int m = (int)(w2start[w + 1] - a); u8 b[12]; size_t st[4];
    tn = 0;
    for (int t = 0; t < m; t++) {
        int i = (i0 + t) % m; unpack12(w2row[a + i], b);
        int ns = portpath(b, w2ty[a + i], p, st);
        if (t == 0) { nst0 = ns; memcpy(st0, st, sizeof st); }
        p = (p + (w2ty[a + i] == 0 ? -1 : 1) + 11) % 11;
    }
    (void)p;
}
static int walk_endport(int w, int i0, int p) {
    u32 a = w2start[w]; int m = (int)(w2start[w + 1] - a);
    for (int t = 0; t < m; t++) { int i = (i0 + t) % m; p = (p + (w2ty[a + i] == 0 ? -1 : 1) + 11) % 11; }
    return p;
}
/* small trail: y (11 letters), letter 11 inserted at position at, written from vertex rot(y, at) */
static void small_trail(const u8 *y, int at) {
    u8 loop[12], B[13]; ins(loop, y, 11, 11, at); tn = 0;
    for (int j = 0; j < 12; j++) { rot(B, loop, 12, at + 1 + j); B[12] = 12; slice(B, 13, 13); }
}

/* ------------------------------------------------------------------ pieces */
static FILE *evf; static u64 nev = 0;
static int last_ov = 0;
/* emit the cyclic word tb[0..tn) from offset c, len letters, overlapping ov letters with what was written */
static void piece(size_t c, size_t len, int ov, const char *desc, int ti) {
    static u8 *pb; static size_t pcap;
    if (len > pcap) { pcap = len * 2; pb = realloc(pb, pcap); }
    for (size_t i = 0, q = c % tn; i < len; i++) { pb[i] = tb[q]; if (++q == tn) q = 0; }
    if (ov > ntail) DIE("overlap %d beyond what was written", ov);
    for (int i = 0; i < ov; i++) if (tail[ntail - ov + i] != pb[i]) DIE("overlap mismatch at letter %llu", (unsigned long long)written);
    if (evf) fprintf(evf, "%llu %llu %llu %d %llu %s %d %llu\n", (unsigned long long)nev, (unsigned long long)(written - ov), (unsigned long long)len, ov, (unsigned long long)tn, desc, ti, (unsigned long long)(c % tn));
    nev++;
    emit(pb + ov, len - ov);
    /* new tail */
    if (len >= HH) { memcpy(tail, pb + len - HH, HH); ntail = HH; }
    else DIE("short piece");
    last_ov = ov;
}
static int maxov(const u8 *S, int cap) {          /* largest q <= cap with tail suffix q == S prefix q */
    if (cap > ntail) cap = ntail;
    for (int q = cap; q > 0; q--) if (!memcmp(tail + ntail - q, S, q)) return q;
    return 0;
}

/* ------------------------------------------------------------------ units */
static u64 cnt_small = 0, cnt_walk = 0;
static void unit_small(int d, int g, int j, int ovcap) {
    const u8 *x = sel[det[d]].x; u8 y[11]; ins(y, x, 10, 10, g);
    char desc[64]; sprintf(desc, "D %d %d %d", d, g, j);
    for (int i = 0; i < 11; i++) {
        int at = (j + i) % 11; small_trail(y, at);
        int ov;
        if (i == 0) ov = written ? maxov(tb, ovcap) : 0; else ov = HH - 1;
        piece(0, tn + HH, ov, desc, i);
        cnt_small++;
    }
}
static void unit_walk(int w, int i0, int way, int ovcap) {
    u32 a = w2start[w]; int m = (int)(w2start[w + 1] - a);
    if (i0 < 0 || i0 >= m || w2ty[a + i0] != 0) DIE("walk unit %d: row %d is not a full row", w, i0);
    char desc[64]; sprintf(desc, "W %d %d %d", w, i0, way);
    for (int i = 0; i < 11; i++) {
        int q = way == 0 ? i : i + 1;               /* gap of the completion letter in row i0 */
        int port = q == 11 ? 0 : q;
        if (walk_endport(w, i0, port) != port) DIE("walk %d: trail of port %d is not one pass", w, port);
        walk_trail(w, i0, port);
        size_t s0 = (q == 11) ? st0[1] : st0[0];
        size_t cut = s0 + (size_t)(q + 1) * (NN + 1);
        int ov;
        if (i == 0) { u8 S[HH]; for (int t = 0; t < HH; t++) S[t] = tb[(cut + t) % tn]; ov = written ? maxov(S, ovcap) : 0; }
        else ov = HH;
        piece(cut, tn + HH + 1, ov, desc, i);
        cnt_walk++;
    }
}

/* ------------------------------------------------------------------ Echols's order */
static void echols(void) {
    /* small groups, detached loops in file order */
    for (int d = 0; d < ndet; d++) {
        const u8 *x = sel[det[d]].x;
        for (int g = 0; g < 10; g++) {
            u8 y[11]; ins(y, x, 10, 10, g);
            for (int i = 0; i < 11; i++) {
                int at = (g + 1 + i) % 11; small_trail(y, at);
                int ov = (g || i) ? (i == 0 ? 9 : 10) : 0;
                char desc[64]; sprintf(desc, "D %d %d %d", d, g, (g + 1) % 11);
                piece(0, tn + HH, ov, desc, i);
                cnt_small++; if (limited) return;
            }
        }
        if (d % 1000 == 999) fprintf(stderr, "  detached %d, letters %llu\n", d + 1, (unsigned long long)written);
    }
    for (int w = 0; w < nw2; w++) {
        u32 a = w2start[w]; int m = (int)(w2start[w + 1] - a), first = -1;
        for (int i = 0; i < m; i++) if (w2ty[a + i] == 0) { first = i; break; }
        if (first < 0) DIE("walk without a full row");
        char desc[64]; sprintf(desc, "W %d %d 0", w, first);
        for (int st = 0; st < 11; st++) {
            if (walk_endport(w, first, st) != st) DIE("completion winding mismatch");
            walk_trail(w, first, st);
            size_t cut = st0[0] + (size_t)(st + 1) * (NN + 1);
            piece(cut, tn + HH + 1, st ? HH : 0, desc, st);
            cnt_walk++; if (limited) return;
        }
        if (w % 1000 == 999) fprintf(stderr, "  walk units %d, letters %llu\n", w + 1, (unsigned long long)written);
    }
}

static unsigned long long expect_len = 0; static char expect_sha[65] = "";
static void plan(const char *path) {
    FILE *f = fopen(path, "r"); if (!f) DIE("cannot open %s", path);
    char line[512]; int nl = 0; u64 nu = 0;
    while (fgets(line, sizeof line, f)) {
        nl++;
        if (nl == 1) { if (strncmp(line, "N14-UNITPLAN", 12)) DIE("not a unit plan"); continue; }
        if (!strncmp(line, "# expect length ", 16)) { if (sscanf(line + 16, "%llu sha256 %64s", &expect_len, expect_sha) < 1) DIE("bad expect line"); continue; }
        if (line[0] == '#' || line[0] == '\n' || line[0] == '\r') continue;
        char k; int a, b, c, cap = HH; char extra[64] = "";
        int got = sscanf(line, " %c %d %d %d %63s", &k, &a, &b, &c, extra);
        if (got < 4) DIE("bad plan line %d", nl);
        if (got == 5 && extra[0] == 'o') cap = atoi(extra + 1);
        if (k == 'D') { if (a < 0 || a >= ndet || b < 0 || b > 9 || c < 0 || c > 10) DIE("bad D line %d", nl); unit_small(a, b, c, cap); }
        else if (k == 'W') { if (a < 0 || a >= nw2 || c < 0 || c > 1) DIE("bad W line %d", nl); unit_walk(a, b, c, cap); }
        else DIE("bad unit kind line %d", nl);
        nu++;
        if (nu % 5000 == 0) fprintf(stderr, "  units %llu, letters %llu\n", (unsigned long long)nu, (unsigned long long)written);
        if (limited) break;
    }
    fclose(f);
}

#ifndef GEN14_NO_MAIN
int main(int argc, char **argv) {
    if (argc < 3) { fprintf(stderr, "usage: gen14 info|echols|plan SEL [PLAN] [--limit L] [--alpha A] [--events EV] [--count] [--nosha]\n"); return 2; }
    double t0 = wall();
    const char *mode = argv[1], *selp = argv[2], *planp = NULL, *alp = NULL, *evp = NULL;
    int ai = 3;
    if (!strcmp(mode, "plan")) { if (argc < 4) DIE("plan needs PLAN"); planp = argv[3]; ai = 4; }
    for (; ai < argc; ai++) {
        if (!strcmp(argv[ai], "--limit") && ai + 1 < argc) limit = strtoull(argv[++ai], NULL, 10);
        else if (!strcmp(argv[ai], "--alpha") && ai + 1 < argc) alp = argv[++ai];
        else if (!strcmp(argv[ai], "--events") && ai + 1 < argc) evp = argv[++ai];
        else if (!strcmp(argv[ai], "--count")) dowrite = 0;
        else if (!strcmp(argv[ai], "--nosha")) dosha = 0;
        else DIE("unknown option %s", argv[ai]);
    }
    if (!alp) alp = !strcmp(mode, "echols") ? "123456789ABCDE" : "0123456789ABCD";
    if (strlen(alp) != 14) DIE("alphabet must have 14 symbols");
    for (int i = 0; i < 14; i++) alpha[i] = (u8)alp[i];
#ifdef _WIN32
    _setmode(_fileno(stdout), _O_BINARY);
#endif
    read_sel(selp);
    build_w2();
    u64 nshort = 0; for (int i = 0; i < nsel; i++) nshort += sel[i].ty == 1;
    fprintf(stderr, "selection: %d loops, %d detached, %llu short, %d walks; 2-lift walks %d with %u rows (%.1fs)\n",
            nsel, ndet, (unsigned long long)nshort, nwalk, nw2, w2start[nw2], wall() - t0);
    u64 F3 = 87178291200ULL + 6227020800ULL + 479001600ULL, Q = 2 * nshort * 10 * 11;
    fprintf(stderr, "F3(14) %llu, Q %llu, sum R = %llu\n", (unsigned long long)F3, (unsigned long long)Q, (unsigned long long)(F3 + Q));
    if (!strcmp(mode, "info")) {
        if (evp) {   /* --events FILE in info mode: table of the 2-lift walks */
            FILE *o = fopen(evp, "w"); if (!o) DIE("cannot write %s", evp);
            for (int w = 0; w < nw2; w++) { u32 a = w2start[w], m = w2start[w + 1] - a, nf = 0; for (u32 i = 0; i < m; i++) nf += w2ty[a + i] == 0;
                fprintf(o, "%d %d %d %d %u %u\n", w, w2walk[w], w2a[w], w2b[w], m, nf); }
            fclose(o);
        }
        return 0;
    }
    obuf = malloc(OBUF); sha_init(&sha);
    if (evp) { evf = fopen(evp, "w"); if (!evf) DIE("cannot open %s", evp); }
    if (!strcmp(mode, "echols")) echols(); else if (!strcmp(mode, "plan")) plan(planp); else DIE("unknown mode");
    flush_out(); fflush(stdout);
    if (evf) fclose(evf);
    char hex[65] = ""; if (dosha) sha_final(&sha, hex);
    fprintf(stderr, "{\"mode\":\"%s\",\"length\":%llu,\"small_trails\":%llu,\"walk_trails\":%llu,\"sha256\":\"%s\",\"alphabet\":\"%s\",\"seconds\":%.0f}\n",
            mode, (unsigned long long)written, (unsigned long long)cnt_small, (unsigned long long)cnt_walk, hex, alp, wall() - t0);
    if (expect_len) {
        int ok = written == expect_len && (!expect_sha[0] || !dosha || !strcmp(expect_sha, hex));
        fprintf(stderr, "expected length %llu%s%s: %s\n", expect_len, expect_sha[0] ? " sha256 " : "", expect_sha, ok ? "MATCH" : "MISMATCH");
        if (!ok) return 3;
    }
    return 0;
}
#endif
