/*
 * demos/shm_ipc.c
 *
 * Demonstrates POSIX shared memory IPC between a parent and child process.
 *
 * Key concept:
 *   Two separate processes (different PIDs, different virtual address spaces)
 *   share the SAME physical memory pages through a named shm object.
 *   Each process's VMA has a DIFFERENT virtual address, but both map
 *   to the same physical frames — this is the difference between virtual
 *   and physical addresses made tangible.
 *
 * Kernel internals:
 *   shm_open() → shmem_open() → creates an anonymous inode in tmpfs
 *   mmap(MAP_SHARED) → vm_area_struct with vm_ops = shmem_vm_ops
 *   After fork(), child inherits the fd but gets a NEW vm_area_struct
 *   pointing to the same struct file → same physical pages.
 *   /proc/sysvipc/shm  shows System V shared memory segments.
 *   For POSIX shm, check /dev/shm/<name> and /proc/<pid>/maps.
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <fcntl.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <sys/wait.h>
#include <time.h>

#define SHM_NAME  "/kernelscope_ipc_demo"
#define SHM_SIZE  4096

typedef struct {
    volatile int  counter;
    volatile int  ready;     /* 1 when parent has written */
    char          message[256];
    pid_t         writer_pid;
    pid_t         reader_pid;
} SharedData;

int main(void) {
    printf("=== KernelScope Shared Memory IPC Demo ===\n\n");

    /* Create (or open) a POSIX shared memory object.
     * This appears as a file under /dev/shm/kernelscope_ipc_demo */
    int shm_fd = shm_open(SHM_NAME, O_CREAT | O_RDWR, 0666);
    if (shm_fd < 0) {
        perror("shm_open");
        return 1;
    }

    if (ftruncate(shm_fd, SHM_SIZE) != 0) {
        perror("ftruncate");
        shm_unlink(SHM_NAME);
        return 1;
    }

    /* Parent maps the shared memory */
    SharedData *shm = mmap(NULL, SHM_SIZE,
                           PROT_READ | PROT_WRITE, MAP_SHARED, shm_fd, 0);
    if (shm == MAP_FAILED) {
        perror("mmap");
        shm_unlink(SHM_NAME);
        return 1;
    }

    memset(shm, 0, SHM_SIZE);
    shm->writer_pid = getpid();

    printf("Parent PID:            %d\n",  getpid());
    printf("Shared memory name:    %s\n",  SHM_NAME);
    printf("Parent mapped at:      %p\n",  (void *)shm);
    printf("Shm file:              /dev/shm%s\n", SHM_NAME);
    printf("\n");
    printf(">> Forking child process...\n\n");
    fflush(stdout);

    pid_t child = fork();

    if (child == 0) {
        /* ── Child process ──────────────────────────────────────────────────
         * The child inherits the shm_fd from fork(), but when it accesses
         * shm, the kernel creates a NEW vm_area_struct for this process.
         * The VMA has a different virtual address but maps to the same
         * physical pages (same struct file, same page cache). */

        shm->reader_pid = getpid();

        printf("  Child PID:           %d  (PPID: %d)\n", getpid(), getppid());
        printf("  Child shm address:   %p\n",  (void *)shm);
        printf("  (Different virtual address, SAME physical pages as parent)\n\n");
        fflush(stdout);

        for (int i = 0; i < 5; i++) {
            /* Spin-wait for parent to set ready flag */
            while (!shm->ready) {
                usleep(10000); /* 10ms */
            }
            printf("  Child reads  [iter %d] counter=%d  msg=\"%s\"\n",
                   i + 1, shm->counter, shm->message);
            fflush(stdout);
            shm->ready = 0; /* acknowledge */
        }

        printf("\nChild exiting.\n");
        munmap(shm, SHM_SIZE);
        close(shm_fd);
        exit(0);
    }

    /* ── Parent process ───────────────────────────────────────────────────── */
    for (int i = 0; i < 5; i++) {
        sleep(1);
        shm->counter = i + 1;
        snprintf(shm->message, 255,
                 "Hello from PID %d, iteration %d, time=%ld",
                 getpid(), i + 1, (long)time(NULL));
        shm->ready = 1; /* signal child */
        printf("Parent writes [iter %d] counter=%d\n", i + 1, shm->counter);
        fflush(stdout);

        /* Wait for child to acknowledge */
        while (shm->ready) {
            usleep(5000);
        }
    }

    /* Wait for child to exit cleanly */
    waitpid(child, NULL, 0);

    printf("\n=== Summary ===\n");
    printf("Parent PID %d and child PID %d communicated via /dev/shm%s\n",
           getpid(), child, SHM_NAME);
    printf("Both had DIFFERENT virtual addresses for the shm region,\n");
    printf("but they mapped to the SAME physical memory frames.\n");
    printf("This is how mmap(MAP_SHARED) works: same struct file,\n");
    printf("different vm_area_struct entries, same page cache pages.\n");

    munmap(shm, SHM_SIZE);
    close(shm_fd);
    shm_unlink(SHM_NAME);

    return 0;
}
