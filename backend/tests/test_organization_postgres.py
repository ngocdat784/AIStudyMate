import os
import unittest
import uuid

from sqlalchemy import delete
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database.connection import engine
from app.models.monitoring import (
    Department,
    Factory,
    Organization,
    Plant,
)


POSTGRES_INTEGRATION_ENABLED = (
    os.environ.get("RUN_POSTGRES_INTEGRATION") == "1"
)


@unittest.skipUnless(
    POSTGRES_INTEGRATION_ENABLED,
    "Set RUN_POSTGRES_INTEGRATION=1 to run PostgreSQL integration tests.",
)
class PostgresOrganizationIntegrationTests(unittest.TestCase):
    def setUp(self):
        if engine.dialect.name != "postgresql":
            self.skipTest("Configured database is not PostgreSQL.")

        self.session = Session(engine)
        self.run_id = uuid.uuid4().hex

        self.organization = Organization(
            name=f"PG Test Organization {self.run_id}"
        )
        self.session.add(self.organization)
        self.session.flush()

        self.factory_a = Factory(
            organization_id=self.organization.id,
            name=f"Factory A {self.run_id}",
        )
        self.factory_b = Factory(
            organization_id=self.organization.id,
            name=f"Factory B {self.run_id}",
        )
        self.session.add_all([self.factory_a, self.factory_b])
        self.session.flush()

        self.department_a = Department(
            factory_id=self.factory_a.id,
            name=f"Department A {self.run_id}",
        )
        self.department_b = Department(
            factory_id=self.factory_b.id,
            name=f"Department B {self.run_id}",
        )
        self.session.add_all([
            self.department_a,
            self.department_b,
        ])
        self.session.commit()

        self.organization_id = self.organization.id
        self.factory_a_id = self.factory_a.id
        self.factory_b_id = self.factory_b.id
        self.department_a_id = self.department_a.id
        self.department_b_id = self.department_b.id

    def tearDown(self):
        if hasattr(self, "session"):
            self.session.rollback()
            self.session.close()

        if not hasattr(self, "organization_id"):
            return

        # Cleanup only records created by this test.
        with Session(engine) as cleanup_session:
            cleanup_session.execute(
                delete(Plant).where(
                    Plant.factory_id.in_([
                        self.factory_a_id,
                        self.factory_b_id,
                    ])
                )
            )

            cleanup_session.execute(
                delete(Department).where(
                    Department.id.in_([
                        self.department_a_id,
                        self.department_b_id,
                    ])
                )
            )

            cleanup_session.execute(
                delete(Factory).where(
                    Factory.id.in_([
                        self.factory_a_id,
                        self.factory_b_id,
                    ])
                )
            )

            cleanup_session.execute(
                delete(Organization).where(
                    Organization.id == self.organization_id
                )
            )
            cleanup_session.commit()

    def _new_plant(self, factory_id, department_id):
        return Plant(
            name=f"PG Test Plant {uuid.uuid4().hex}",
            factory_id=factory_id,
            department_id=department_id,
        )

    def _assert_constraint(self, error_context, expected_name):
        original_error = error_context.exception.orig
        diagnostic = getattr(original_error, "diag", None)
        actual_name = getattr(
            diagnostic,
            "constraint_name",
            None,
        )

        self.assertEqual(
            actual_name,
            expected_name,
            msg=(
                f"Expected PostgreSQL constraint {expected_name}, "
                f"got {actual_name}. Original error: {original_error}"
            ),
        )

    def test_valid_same_factory_department_is_accepted(self):
        plant = self._new_plant(
            self.factory_a_id,
            self.department_a_id,
        )

        self.session.add(plant)
        self.session.commit()

        self.assertIsNotNone(plant.id)

    def test_cross_factory_department_is_rejected(self):
        plant = self._new_plant(
            self.factory_b_id,
            self.department_a_id,
        )

        self.session.add(plant)

        with self.assertRaises(IntegrityError) as context:
            self.session.flush()

        self.session.rollback()

        self._assert_constraint(
            context,
            "fk_plant_department_factory",
        )

    def test_department_without_factory_is_rejected(self):
        plant = self._new_plant(
            None,
            self.department_a_id,
        )

        self.session.add(plant)

        with self.assertRaises(IntegrityError) as context:
            self.session.flush()

        self.session.rollback()

        self._assert_constraint(
            context,
            "ck_plant_department_requires_factory",
        )

    def test_nonexistent_department_is_rejected(self):
        plant = self._new_plant(
            self.factory_a_id,
            -999999999,
        )

        self.session.add(plant)

        with self.assertRaises(IntegrityError) as context:
            self.session.flush()

        self.session.rollback()

        self._assert_constraint(
            context,
            "fk_monitoring_plants_department_id_departments",
        )

    def test_department_delete_is_restricted_when_plant_references_it(self):
        plant = self._new_plant(
            self.factory_a_id,
            self.department_a_id,
        )
        self.session.add(plant)
        self.session.commit()

        with self.assertRaises(IntegrityError):
            self.session.execute(
                delete(Department).where(
                    Department.id == self.department_a_id
                )
            )
            self.session.flush()

        self.session.rollback()

    def test_factory_delete_is_restricted_when_plant_references_it(self):
        plant = self._new_plant(
            self.factory_a_id,
            self.department_a_id,
        )
        self.session.add(plant)
        self.session.commit()

        with self.assertRaises(IntegrityError):
            self.session.execute(
                delete(Factory).where(
                    Factory.id == self.factory_a_id
                )
            )
            self.session.flush()

        self.session.rollback()

    def test_organization_delete_is_restricted_when_factory_has_plant(self):
        plant = self._new_plant(
            self.factory_a_id,
            self.department_a_id,
        )
        self.session.add(plant)
        self.session.commit()

        with self.assertRaises(IntegrityError):
            self.session.execute(
                delete(Organization).where(
                    Organization.id == self.organization_id
                )
            )
            self.session.flush()

        self.session.rollback()

    def test_duplicate_department_name_in_same_factory_is_rejected(self):
        duplicate = Department(
            factory_id=self.factory_a_id,
            name=f"Department A {self.run_id}",
        )
        self.session.add(duplicate)

        with self.assertRaises(IntegrityError) as context:
            self.session.flush()

        self.session.rollback()

        self._assert_constraint(
            context,
            "uq_department_factory_name",
        )

    def test_same_department_name_in_different_factories_is_allowed(self):
        department_a = Department(
            factory_id=self.factory_a_id,
            name=f"Shared Department {self.run_id}",
        )
        department_b = Department(
            factory_id=self.factory_b_id,
            name=f"Shared Department {self.run_id}",
        )

        self.session.add_all([department_a, department_b])
        self.session.commit()

        self.assertIsNotNone(department_a.id)
        self.assertIsNotNone(department_b.id)
        self.assertNotEqual(department_a.id, department_b.id)


if __name__ == "__main__":
    unittest.main()
