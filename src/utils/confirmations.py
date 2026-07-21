import sys
from typing import Optional
from reporting.logger import MigrateLogger


class ConfirmationManager:
    def __init__(self, logger: MigrateLogger):
        self.logger = logger

    def confirm_once(
        self,
        message: str,
        expected_input: str = "confirm",
        timeout_seconds: Optional[int] = None
    ) -> bool:
        print(f"\n{message}")
        print(f"Type '{expected_input}' to continue or anything else to cancel: ", end='')
        sys.stdout.flush()

        try:
            user_input = input().strip()
            confirmed = user_input.lower() == expected_input.lower()

            if confirmed:
                self.logger.info("User confirmed action")
            else:
                self.logger.info(f"User cancelled action (input: '{user_input}')")

            return confirmed

        except KeyboardInterrupt:
            print("\n")
            self.logger.info("User cancelled action with Ctrl+C")
            return False
        except EOFError:
            print("\n")
            self.logger.info("User cancelled action (EOF)")
            return False

    def confirm_twice(
        self,
        message: str,
        expected_input: str = "confirm"
    ) -> bool:
        print(f"\n{'=' * 70}")
        print("WARNING: DESTRUCTIVE OPERATION")
        print(f"{'=' * 70}")
        print(f"\n{message}")
        print(f"\nThis operation is IRREVERSIBLE.")
        print(f"{'=' * 70}\n")

        print(f"First confirmation - Type '{expected_input}' to continue: ", end='')
        sys.stdout.flush()

        try:
            user_input_1 = input().strip()
            if user_input_1.lower() != expected_input.lower():
                self.logger.info(f"User cancelled at first confirmation (input: '{user_input_1}')")
                print("\nOperation cancelled.")
                return False

            print(f"\nSecond confirmation - Type '{expected_input}' again: ", end='')
            sys.stdout.flush()

            user_input_2 = input().strip()
            confirmed = user_input_2.lower() == expected_input.lower()

            if confirmed:
                self.logger.info("User confirmed action (double confirmation)")
                print("\nConfirmation received. Proceeding...\n")
            else:
                self.logger.info(f"User cancelled at second confirmation (input: '{user_input_2}')")
                print("\nOperation cancelled.")

            return confirmed

        except KeyboardInterrupt:
            print("\n")
            self.logger.info("User cancelled action with Ctrl+C")
            print("\nOperation cancelled.")
            return False
        except EOFError:
            print("\n")
            self.logger.info("User cancelled action (EOF)")
            print("\nOperation cancelled.")
            return False

    def confirm_clone(self, num_users: int) -> bool:
        message = (
            "CLONE WORKFLOW\n"
            "--------------\n"
            f"This will clone {num_users} user(s) to new usernames.\n\n"
            "What will happen:\n"
            "  - New users will be created (or reused if they exist)\n"
            "  - Permissions will be replicated\n"
            "  - Groups will be replicated\n"
            "  - UX artifacts (favorites, subscriptions, etc.) will be replicated\n"
            "  - Content ownership will NOT be reassigned\n"
            "  - Old users will remain active and licensed\n\n"
            "Additional licenses will be consumed."
        )
        return self.confirm_once(message)

    def confirm_migrate(self, num_users: int) -> bool:
        message = (
            "MIGRATE WORKFLOW\n"
            "----------------\n"
            f"This will fully migrate {num_users} user(s) to new usernames.\n\n"
            "What will happen:\n"
            "  - New users will be created (or reused if they exist)\n"
            "  - Content ownership will be REASSIGNED\n"
            "  - Permissions will be replicated then removed from old users\n"
            "  - Groups will be replicated then removed from old users\n"
            "  - UX artifacts will be replicated then removed from old users\n"
            "  - Old users will be UNLICENSED and DEACTIVATED\n\n"
            "This is a complete end-to-end migration."
        )
        return self.confirm_twice(message)

    def confirm_cleanup(self, num_users: int) -> bool:
        message = (
            "CLEAN-ONLY WORKFLOW\n"
            "-------------------\n"
            f"This will clean up {num_users} user(s).\n\n"
            "What will happen:\n"
            "  - ALL permissions will be REMOVED\n"
            "  - ALL group memberships will be REMOVED\n"
            "  - ALL UX artifacts will be REMOVED\n"
            "  - Users will be UNLICENSED\n\n"
            "This assumes users have already been cloned/migrated!\n"
            "No new users will be created.\n"
            "No content will be reassigned."
        )
        return self.confirm_twice(message)
