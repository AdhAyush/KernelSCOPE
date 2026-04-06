/*
 * demos/zero_copy.c
 *
 * Benchmarks sendfile() (zero-copy) vs read()+write() (conventional copy).
 *
 * Zero-copy concept:
 *   Conventional:  disk → page cache → user buffer → socket buffer → NIC
 *   sendfile():    disk → page cache ─────────────→ socket buffer → NIC
 *                                    (no user-space buffer involved)
 *
 *   sendfile() stays entirely in ring 0 (kernel mode) for the data transfer.
 *   The CPU never touches the data bytes in user space.
 *   DMA (Direct Memory Access) moves data between page cache and NIC ring buffer
 *   without CPU involvement at all on modern hardware.
 *
 * Kernel internals:
 *   sendfile(2) → do_sendfile() → in_file->f_op->sendpage()
 *   or on Linux 6+: copy_file_range() path
 *   The data never crosses the kernel/user boundary — no page mapping to user VA.
 *
 * What the strace panel will show:
 *   sendfile method:  only sendfile() syscalls — no read() or write()
 *   read/write method: many read() + write() pairs, each crossing ring 3→0
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <fcntl.h>
#include <unistd.h>
#include <sys/sendfile.h>
#include <sys/stat.h>
#include <time.h>
#include <errno.h>

#define FILE_SIZE   (50 * 1024 * 1024)   /* 50 MB */
#define BUFFER_SIZE (64  * 1024)          /* 64 KB read/write chunk */

static long elapsed_ms(struct timespec s, struct timespec e) {
    return (e.tv_sec - s.tv_sec) * 1000L +
           (e.tv_nsec - s.tv_nsec) / 1000000L;
}

static int make_source_file(char *path, size_t size) {
    int fd = mkstemp(path);
    if (fd < 0) { perror("mkstemp (source)"); return -1; }

    char *buf = malloc(BUFFER_SIZE);
    if (!buf) { close(fd); return -1; }
    memset(buf, 0x55, BUFFER_SIZE);   /* fill with pattern 0x55 */

    size_t written = 0;
    while (written < size) {
        size_t chunk = (size - written < BUFFER_SIZE) ? (size - written) : BUFFER_SIZE;
        ssize_t n = write(fd, buf, chunk);
        if (n <= 0) break;
        written += n;
    }
    free(buf);
    fsync(fd);
    return fd;
}

int main(void) {
    printf("=== KernelScope Zero-Copy Benchmark ===\n\n");

    /* ── Create source file ─────────────────────────────────────────────────── */
    char src_path[] = "/tmp/ks_zerocopy_src_XXXXXX";
    char dst1_path[] = "/tmp/ks_zerocopy_sf_XXXXXX";
    char dst2_path[] = "/tmp/ks_zerocopy_rw_XXXXXX";

    printf("Creating %d MB source file...\n", FILE_SIZE / 1024 / 1024);
    int src_fd = make_source_file(src_path, FILE_SIZE);
    if (src_fd < 0) return 1;
    printf("Source: %s\n\n", src_path);

    /* ── Method 1: sendfile() ───────────────────────────────────────────────── */
    printf("Method 1: sendfile()  [zero-copy — data stays in kernel]\n");
    printf("  Data path: page cache → socket/file buffer (no user buffer)\n");

    int dst1_fd = mkstemp(dst1_path);
    if (dst1_fd < 0) { perror("mkstemp dst1"); return 1; }

    lseek(src_fd, 0, SEEK_SET);

    struct timespec t0, t1;
    clock_gettime(CLOCK_MONOTONIC, &t0);

    off_t offset    = 0;
    ssize_t remaining = FILE_SIZE;
    while (remaining > 0) {
        ssize_t sent = sendfile(dst1_fd, src_fd, &offset, (size_t)remaining);
        if (sent < 0) {
            if (errno == EINTR) continue;
            perror("sendfile");
            break;
        }
        remaining -= sent;
    }
    fsync(dst1_fd);

    clock_gettime(CLOCK_MONOTONIC, &t1);
    long sendfile_ms = elapsed_ms(t0, t1);

    printf("  Time: %ld ms\n\n", sendfile_ms);

    /* ── Method 2: read() + write() loop ────────────────────────────────────── */
    printf("Method 2: read()+write()  [conventional — 2 copies through user space]\n");
    printf("  Data path: page cache → user buffer → kernel buffer → disk\n");

    int dst2_fd = mkstemp(dst2_path);
    if (dst2_fd < 0) { perror("mkstemp dst2"); return 1; }

    char *buf = malloc(BUFFER_SIZE);
    if (!buf) { perror("malloc"); return 1; }

    lseek(src_fd, 0, SEEK_SET);

    clock_gettime(CLOCK_MONOTONIC, &t0);

    ssize_t n;
    while ((n = read(src_fd, buf, BUFFER_SIZE)) > 0) {
        ssize_t written = 0;
        while (written < n) {
            ssize_t w = write(dst2_fd, buf + written, n - written);
            if (w < 0) { if (errno == EINTR) continue; break; }
            written += w;
        }
    }
    fsync(dst2_fd);

    clock_gettime(CLOCK_MONOTONIC, &t1);
    long readwrite_ms = elapsed_ms(t0, t1);

    printf("  Time: %ld ms\n\n", readwrite_ms);

    /* ── Results ─────────────────────────────────────────────────────────────── */
    printf("=== Results ===\n");
    printf("  sendfile():     %5ld ms\n", sendfile_ms);
    printf("  read()+write(): %5ld ms\n", readwrite_ms);

    if (sendfile_ms > 0 && readwrite_ms > 0) {
        printf("  Speedup:        %.2fx\n",
               (double)readwrite_ms / (double)sendfile_ms);
    }

    printf("\nWhy sendfile() is faster:\n");
    printf("  - No data copied to user-space buffer (saves 1 memcpy per chunk)\n");
    printf("  - Fewer context switches (ring 3 ↔ ring 0) per byte transferred\n");
    printf("  - On systems with DMA gather: zero CPU cycles touching the data\n");
    printf("  - Read the strace panel in KernelScope: sendfile shows NO read()/write()\n");

    /* Cleanup */
    free(buf);
    close(src_fd);  unlink(src_path);
    close(dst1_fd); unlink(dst1_path);
    close(dst2_fd); unlink(dst2_path);

    return 0;
}
