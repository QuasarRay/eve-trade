package distributedbackend

import (
	"context"
	"errors"
	"fmt"

	"connectrpc.com/connect"
)

func downstreamUnavailable(service string, err error) error {
	if err == nil {
		return nil
	}

	code := connect.CodeOf(err)
	switch code {
	case connect.CodeUnavailable:
		return err
	case connect.CodeUnknown:
		if errors.Is(err, context.Canceled) {
			return connect.NewError(connect.CodeCanceled, fmt.Errorf("%s request canceled: %w", service, err))
		}
		return connect.NewError(connect.CodeUnavailable, fmt.Errorf("%s unavailable: %w", service, err))
	default:
		return err
	}
}
