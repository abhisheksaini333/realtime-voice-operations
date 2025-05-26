package main

import (
	"context"
	"encoding/json"
	"fmt"
	siptest "github.com/abhisheksaini333/realtime-voice-operations/fixtures/sipphone"
	"github.com/emiago/sipgo/sip"
	"net/netip"
	"os"
	"time"
)

func emit(v interface{}) { json.NewEncoder(os.Stdout).Encode(v) }
func media(ctx context.Context, c *siptest.Client) error {
	done := make(chan error, 1)
	go func() { done <- c.WaitSignals(ctx, []int{5}, nil) }()
	select {
	case err := <-done:
		return err
	case <-ctx.Done():
		return ctx.Err()
	}
}
func main() {
	ctx, cancel := context.WithTimeout(context.Background(), 40*time.Second)
	defer cancel()
	mode := "operator"
	if len(os.Args) > 1 {
		mode = os.Args[1]
	}
	refs := make(chan *sip.Request, 1)
	byes := make(chan bool, 1)
	cli, err := siptest.NewClient("initial", siptest.ClientConfig{IP: netip.MustParseAddr("127.0.0.1"), Number: "1001", AuthUser: "voiceops", AuthPass: "local-pass", OnRefer: func(req *sip.Request) { refs <- req }, OnBye: func() {
		select {
		case byes <- true:
		default:
		}
	}})
	if err != nil {
		panic(err)
	}
	defer cli.Close()
	if err = cli.Dial("127.0.0.1:18150", "127.0.0.1", "2000", nil); err != nil {
		panic(err)
	}
	go cli.SendSignal(ctx, 0, 5)
	if err = media(ctx, cli); err != nil {
		panic(err)
	}
	cli.SendDTMF("5")
	emit(map[string]interface{}{"stage": "ready", "initial_audio": true})
	var req *sip.Request
	select {
	case req = <-refs:
	case <-ctx.Done():
		panic(ctx.Err())
	}
	expected := "sip:" + mode + "@127.0.0.1:18160"
	if req.GetHeader("Refer-To").Value() != expected {
		panic("unexpected transfer target")
	}
	time.Sleep(100 * time.Millisecond)
	cli.SendNotify(req, "SIP/2.0 100 Trying")
	second, err := siptest.NewClient("second", siptest.ClientConfig{IP: netip.MustParseAddr("127.0.0.1"), Number: "1001"})
	if err != nil {
		panic(err)
	}
	defer second.Close()
	start := time.Now()
	err = second.Dial("127.0.0.1:18160", "127.0.0.1", mode, nil)
	if mode == "operator" {
		if err != nil {
			panic(err)
		}
		go second.SendSignal(ctx, 0, 5)
		if err = media(ctx, second); err != nil {
			panic(err)
		}
		cli.SendNotify(req, "SIP/2.0 200 OK")
		select {
		case <-byes:
		case <-ctx.Done():
			panic("original BYE not received")
		}
		if err = media(ctx, second); err != nil {
			panic(err)
		}
		emit(map[string]interface{}{"stage": "complete", "outcome": "connected", "second_answered": true, "second_audio": true, "original_bye": true, "elapsed_ms": time.Since(start).Milliseconds(), "refer_call_id": req.CallID().Value()})
	} else {
		if err == nil {
			panic("failure endpoint unexpectedly answered")
		}
		status := "SIP/2.0 486 Busy Here"
		if mode == "unanswered" {
			status = "SIP/2.0 408 Request Timeout"
		}
		cli.SendNotify(req, status)
		if err = media(ctx, cli); err != nil {
			panic(err)
		}
		select {
		case <-byes:
			panic("original call ended after failed transfer")
		case <-time.After(time.Second):
		}
		emit(map[string]interface{}{"stage": "complete", "outcome": mode, "original_audio": true, "original_bye": false, "elapsed_ms": time.Since(start).Milliseconds(), "refer_call_id": req.CallID().Value()})
	}
	fmt.Fprintln(os.Stderr, "transfer fixture completed")
}
