package rabbitmqsettlement

import (
	"context"
	"errors"
	"fmt"

	amqp "github.com/rabbitmq/amqp091-go"
)

var errPublishNotConfirmed = errors.New("rabbitmq publish was not confirmed")

func enablePublisherConfirms(channel *amqp.Channel) (<-chan amqp.Confirmation, error) {
	if err := channel.Confirm(false); err != nil {
		return nil, fmt.Errorf("enable publisher confirms: %w", err)
	}
	return channel.NotifyPublish(make(chan amqp.Confirmation, 64)), nil
}

func publishConfirmed(
	ctx context.Context,
	channel *amqp.Channel,
	confirms <-chan amqp.Confirmation,
	exchange string,
	routingKey string,
	mandatory bool,
	message amqp.Publishing,
) error {
	if err := channel.PublishWithContext(ctx, exchange, routingKey, mandatory, false, message); err != nil {
		return err
	}

	select {
	case confirmation, ok := <-confirms:
		if !ok {
			return errors.New("rabbitmq publisher confirmation channel closed")
		}
		if !confirmation.Ack {
			return errPublishNotConfirmed
		}
		return nil
	case <-ctx.Done():
		return ctx.Err()
	}
}
