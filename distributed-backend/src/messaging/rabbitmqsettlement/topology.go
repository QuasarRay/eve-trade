package rabbitmqsettlement

import (
	"fmt"

	amqp "github.com/rabbitmq/amqp091-go"
)

func setupTopology(channel *amqp.Channel, config Config) error {
	config = config.WithDefaults()

	if err := channel.ExchangeDeclare(
		config.Exchange,
		"direct",
		true,
		false,
		false,
		false,
		nil,
	); err != nil {
		return fmt.Errorf("declare settlement exchange: %w", err)
	}
	if err := channel.ExchangeDeclare(
		config.DeadLetterExchange,
		"direct",
		true,
		false,
		false,
		false,
		nil,
	); err != nil {
		return fmt.Errorf("declare settlement dead-letter exchange: %w", err)
	}

	if _, err := channel.QueueDeclare(
		config.DeadLetterQueue,
		true,
		false,
		false,
		false,
		amqp.Table{"x-queue-type": "quorum"},
	); err != nil {
		return fmt.Errorf("declare settlement dead-letter queue: %w", err)
	}
	if err := channel.QueueBind(
		config.DeadLetterQueue,
		config.DeadLetterRoutingKey,
		config.DeadLetterExchange,
		false,
		nil,
	); err != nil {
		return fmt.Errorf("bind settlement dead-letter queue: %w", err)
	}

	commandArgs := amqp.Table{
		"x-queue-type":              "quorum",
		"x-dead-letter-exchange":    config.DeadLetterExchange,
		"x-dead-letter-routing-key": config.DeadLetterRoutingKey,
	}
	if _, err := channel.QueueDeclare(
		config.CommandQueue,
		true,
		false,
		false,
		false,
		commandArgs,
	); err != nil {
		return fmt.Errorf("declare settlement command queue: %w", err)
	}
	if err := channel.QueueBind(
		config.CommandQueue,
		config.RoutingKey,
		config.Exchange,
		false,
		nil,
	); err != nil {
		return fmt.Errorf("bind settlement command queue: %w", err)
	}

	return nil
}
