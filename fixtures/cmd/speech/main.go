// Local synthetic-speech probe; no external routes or destinations.
package main

import (
	"bufio"
	"encoding/json"
	"fmt"
	siptest "github.com/abhisheksaini333/realtime-voice-operations/fixtures/sipphone"
	"net/netip"
	"os"
	"time"
)

func main() {
	if len(os.Args) != 3 {
		panic("usage: speech input.webm received.webm")
	}
	done := make(chan struct{})
	go func() {
		select {
		case <-done:
			return
		case <-time.After(65 * time.Second):
			os.Exit(2)
		}
	}()
	defer close(done)
	cli, err := siptest.NewClient("speech", siptest.ClientConfig{IP: netip.MustParseAddr("127.0.0.1"), Number: "1003", AuthUser: "voiceops", AuthPass: "local-pass"})
	if err != nil {
		panic(err)
	}
	defer cli.Close()
	if err = cli.Dial("127.0.0.1:18150", "127.0.0.1", "2002", nil); err != nil {
		panic(err)
	}
	output, err := os.Create(os.Args[2])
	if err != nil {
		panic(err)
	}
	cli.Record(output)
	json.NewEncoder(os.Stdout).Encode(map[string]string{"stage": "ready"})
	scanner := bufio.NewScanner(os.Stdin)
	if !scanner.Scan() {
		panic("missing start signal")
	}
	if err = cli.SendAudio(os.Args[1]); err != nil {
		panic(err)
	}
	if err = cli.SendDTMF("#"); err != nil {
		panic(err)
	}
	json.NewEncoder(os.Stdout).Encode(map[string]string{"stage": "spoken"})
	if !scanner.Scan() {
		panic("missing finish signal")
	}
	fmt.Fprintln(os.Stderr, "speech fixture completed")
}
