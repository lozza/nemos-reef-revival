/* Compiled soft-float C math imports for the ARM32 guest.

   Mirrors desktop/guest_math.py MathBindings.call: arguments are the raw
   R0..R3 words (floats in R0/R1, doubles in R0:R1 and R2:R3), the exact host
   C library function supplied by the caller does the arithmetic (never a
   compiler intrinsic), and host errno is swapped exactly like ctypes'
   use_errno: saved, cleared for the call, captured, restored. EDOM/ERANGE
   become the ARM errno in one guest write; a double result then writes the
   high word to R1 and returns the low word. Anything the Python path could
   treat differently (float signalling NaNs, other host errno values) returns
   0 before the guest is touched, so Python handles that call itself. */
#include <errno.h>
#include <string.h>
#include "bridge_api.h"

#define NEMO_MATH_FIRST 201u
#define NEMO_MATH_COUNT 21u
#define NEMO_FLOAT 1u
#define NEMO_BINARY 2u
/* guest_errno.EDOM / ERANGE: ARM/Linux values, independent of the host. */
#define GUEST_EDOM 33u
#define GUEST_ERANGE 34u

typedef int *(*ErrnoFunction)(void);
typedef double (*UnaryDouble)(double);
typedef double (*BinaryDouble)(double, double);
typedef float (*UnaryFloat)(float);
typedef float (*BinaryFloat)(float, float);

/* Operations 201..221, in compiled_bridge.py name order. */
static const unsigned char kinds[NEMO_MATH_COUNT] = {
    0,                       /* acos */
    NEMO_FLOAT,              /* acosf */
    NEMO_FLOAT,              /* asinf */
    NEMO_FLOAT,              /* ceilf */
    0,                       /* cos */
    NEMO_FLOAT,              /* cosf */
    0,                       /* floor */
    NEMO_FLOAT,              /* floorf */
    0,                       /* log */
    NEMO_FLOAT,              /* roundf */
    0,                       /* sin */
    NEMO_FLOAT,              /* sinf */
    0,                       /* sqrt */
    NEMO_FLOAT,              /* sqrtf */
    NEMO_FLOAT,              /* tanf */
    NEMO_FLOAT,              /* truncf */
    NEMO_BINARY,             /* fmax */
    NEMO_FLOAT|NEMO_BINARY,  /* fmaxf */
    NEMO_BINARY,             /* fmin */
    NEMO_FLOAT|NEMO_BINARY,  /* fmodf */
    NEMO_BINARY,             /* pow */
};

/* Python widens float32 to double and back (struct "f" then c_float), which
   quiets a signalling NaN in a Python-version-dependent way; keep it there. */
static int signalling_float(uint32_t bits)
{
    return (bits & 0x7f800000u) == 0x7f800000u && (bits & 0x007fffffu) != 0
        && (bits & 0x00400000u) == 0;
}

static float float_bits(uint32_t bits)
{
    float value;
    memcpy(&value, &bits, sizeof value);
    return value;
}

static double double_bits(uint32_t low, uint32_t high)
{
    uint64_t bits = (uint64_t)high << 32 | low;
    double value;
    memcpy(&value, &bits, sizeof value);
    return value;
}

int nemo_math_call(const NemoApi *api, void *engine, uint32_t operation,
                   const uint32_t args[4], void *math_function, void *errno_function,
                   uint32_t guest_errno, uint32_t *result, int *error)
{
    unsigned kind;
    int *place;
    int saved, code, status;
    uint32_t low, high = 0, code_word;
    uintptr_t function = (uintptr_t)math_function;

    if (api == NULL || args == NULL || result == NULL || error == NULL
        || math_function == NULL || errno_function == NULL
        || api->mem_write == NULL || api->reg_write == NULL)
        return 0;
    if (operation < NEMO_MATH_FIRST || operation - NEMO_MATH_FIRST >= NEMO_MATH_COUNT)
        return 0;
    kind = kinds[operation - NEMO_MATH_FIRST];
    if ((kind & NEMO_FLOAT) && (signalling_float(args[0])
                                || ((kind & NEMO_BINARY) && signalling_float(args[1]))))
        return 0;
    place = ((ErrnoFunction)(uintptr_t)errno_function)();
    if (place == NULL)
        return 0;

    saved = *place;
    *place = 0;
    switch (kind) {
    case 0: {
        double value = ((UnaryDouble)function)(double_bits(args[0], args[1]));
        memcpy(&low, &value, sizeof low);
        memcpy(&high, (const unsigned char *)&value + 4, sizeof high);
        break;
    }
    case NEMO_BINARY: {
        double value = ((BinaryDouble)function)(double_bits(args[0], args[1]),
                                                double_bits(args[2], args[3]));
        memcpy(&low, &value, sizeof low);
        memcpy(&high, (const unsigned char *)&value + 4, sizeof high);
        break;
    }
    case NEMO_FLOAT: {
        float value = ((UnaryFloat)function)(float_bits(args[0]));
        memcpy(&low, &value, sizeof low);
        break;
    }
    default: {
        float value = ((BinaryFloat)function)(float_bits(args[0]), float_bits(args[1]));
        memcpy(&low, &value, sizeof low);
        break;
    }
    }
    code = *place;
    *place = saved;

    if ((kind & NEMO_FLOAT) && signalling_float(low))
        return 0;  /* ctypes would quiet it on return; the call is pure, Python repeats it */
    if (code == EDOM)
        code_word = GUEST_EDOM;
    else if (code == ERANGE)
        code_word = GUEST_ERANGE;
    else if (code == 0)
        code_word = 0;
    else
        return 0;  /* guest_errno.from_host decides (or raises) in Python */

    if (code_word) {
        status = api->mem_write(engine, guest_errno, &code_word, sizeof code_word);
        if (status != 0) {
            *error = status;
            return -1;
        }
    }
    if (!(kind & NEMO_FLOAT)) {
        status = api->reg_write(engine, api->regs[1], &high);
        if (status != 0) {
            *error = status;
            return -1;
        }
    }
    *result = low;
    return 1;
}
