// Compile in a Visual Studio developer prompt:
// cl /EHsc /std:c++17 tests/test_pad_stream.cpp /Fe:work/test_pad_stream.exe
#ifdef _MSC_VER
#define __attribute__(value)
#endif
#include <assert.h>
#include "../firmware/Switch2Dual/PadStream.h"

int main() {
  const char *id="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
  PadStream s;
  PadStream::Frame frames[2];
  frames[0].report.buttons=4;
  assert(!s.accept(id,0,frames,2,100));
  assert(s.count==2 && s.nextSequence==1);
  // Duplicate acknowledgement cannot press twice or extend the lease.
  assert(!s.accept(id,0,frames,2,200));
  assert(s.count==2 && s.receivedAt==100);
  assert(s.accept(id,3,frames,1,200));
  s.tick(101);assert(s.report.buttons==4);
  const uint32_t gen=s.generation;
  s.acknowledge(gen,102,false);s.tick(150);
  assert(s.count==2 && s.report.buttons==4); // Never skip an unobserved short press.
  s.acknowledge(gen,151,true);
  s.tick(166);assert(s.report.buttons==4);
  s.tick(167);assert(s.count==1 && s.report.buttons==0);
  s.acknowledge(s.generation,168,true);
  s.tick(184);assert(s.idle());
  // A fresh transition after idle must be sent, not mistaken for an old ACK.
  assert(!s.accept(id,1,frames,1,185));s.tick(185);
  assert(s.count==1 && s.report.buttons==4 && !s.acknowledged);
  s.acknowledge(gen,186,true);assert(!s.acknowledged); // Stale ACK.
  assert(!s.expired(684));assert(s.expired(685)); // Queue age bound.
  s.stop();assert(!s.active && s.report.buttons==0);
  assert(s.accept(id,1,frames,1,700)); // Expired session cannot resume at sequence 1.
  assert(!s.accept(id,0,frames,1,UINT32_MAX-20));
  s.tick(UINT32_MAX-19);s.acknowledge(s.generation,UINT32_MAX-18,true);
  s.tick(0);assert(s.count==0 && s.report.buttons==4);
  assert(!s.expired(578));assert(s.expired(579)); // Lease across millis wrap.
  s.stop();
  PadStream::Frame full[PadStream::Capacity];
  assert(!s.accept(id,0,full,PadStream::Capacity,0));
  assert(s.accept(id,1,frames,1,1));assert(s.count==PadStream::Capacity);
  return 0;
}
